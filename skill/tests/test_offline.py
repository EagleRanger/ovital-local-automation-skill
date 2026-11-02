"""Synthetic tests. Never construct WinMessageTransport or contact Ovital."""
import ctypes as C
from io import BytesIO
import json
from pathlib import Path
import struct
import sys
from tempfile import TemporaryDirectory
import unittest
import contextlib
import importlib.util
from io import StringIO

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from shapely.geometry import Polygon,LineString,Point,box,mapping,shape
from PIL import Image
from ovital import protocol as P,geometry as G,kml,imagery as I
from ovital.favorites import Favorites,save_and_verify,source_equal

class MemoryTransport:
    """Wire-shaped fake. Effects exist only in this in-memory object store."""
    allow_write=True
    def __init__(self,store=None):self.store=store if store is not None else {};self.counter=100;self.calls=[]
    def request(self,cmd,data=b'',**options):
        self.calls.append(cmd)
        if cmd==20100:
            parent=P.parse_header(data)['id'];out=P.header()
            for oid,raw in sorted(self.store.items()):
                h=P.parse_header(raw)
                if h['parent_id']==parent:out+=struct.pack('<Ii',oid,h['type'])+P.object_name(raw).encode().ljust(100,b'\0')
            return 1,out
        if cmd==20101:return 1,self.store[P.parse_header(data)['id']]
        if cmd==20102:
            h=P.parse_header(data);oid=h['id']
            if not oid:self.counter+=1;oid=self.counter
            raw=bytearray(data);struct.pack_into('<I',raw,8,oid);self.store[oid]=bytes(raw);return oid,None
        if cmd==20108:
            parent,index=struct.unpack_from('<Ii',data,16)
            for offset in range(24,len(data),4):
                oid=struct.unpack_from('<I',data,offset)[0];raw=bytearray(self.store[oid]);struct.pack_into('<I',raw,4,parent);self.store[oid]=bytes(raw)
            return 1,None
        if cmd==20103:
            for offset in range(16,len(data),4):self.store.pop(struct.unpack_from('<I',data,offset)[0])
            return 1,None
        raise AssertionError('unexpected command')
    def __enter__(self):return self
    def __exit__(self,*args):pass

class NativeProtocolTests(unittest.TestCase):
    def setUp(self):self.line=LineString([(1.,1.),(1.001,1.001)])
    def raw(self,geometry=None):
        typ,body=P.geometry_body('Synthetic',self.line if geometry is None else geometry,comment='note')
        return P.header(typ,parent=10,oid=20)+body
    def test_abi_sizes(self):
        self.assertEqual([C.sizeof(s) for s in [P.View3D,P.Draw,P.Track,P.Shape,P.Group,I.MapInfo,I.MapFile]],[80,16,336,352,436,104,472]);self.assertEqual(P.HEADER.size,16);self.assertEqual(P.POINT.size,48)
    def test_track_roundtrip(self):
        row=P.decode(self.raw());self.assertEqual(row['parts'],[[[1.,1.],[1.001,1.001]]]);self.assertEqual(row['comment'],'note');self.assertEqual(row['name'],'Synthetic')
    def test_polygon_holes_roundtrip(self):
        geometry=Polygon([(0,0),(1,0),(1,1),(0,1),(0,0)],[[(.2,.2),(.2,.4),(.4,.4),(.4,.2),(.2,.2)]])
        result=P.decode(self.raw(geometry));self.assertTrue(shape(result['geometry']).equals(geometry));self.assertEqual(len(result['parts']),2)
    def test_multiline_parts_roundtrip(self):
        from shapely.geometry import MultiLineString
        geometry=MultiLineString([[(0,0),(1,0)],[(1,1),(2,1)]])
        row=P.decode(self.raw(geometry));self.assertEqual(len(row['parts']),2)
    def test_group_roundtrip(self):self.assertEqual(P.decode(P.header(30)+P.group_body('Synthetic group',hidden=True))['visibility']['show'],32)
    def test_utf8_name_limit(self):
        with self.assertRaises(ValueError):P.name_bytes('测'*34)
    def test_version_refused(self):
        raw=bytearray(self.raw());struct.pack_into('<H',raw,0,999)
        with self.assertRaises(ValueError):P.decode(raw)
    def test_payload_truncation_refused(self):
        with self.assertRaises(ValueError):P.decode(self.raw()[:-1])
    def test_wrong_count_refused(self):
        raw=bytearray(self.raw());struct.pack_into('<i',raw,16+P.Track.count.offset,999)
        with self.assertRaises(ValueError):P.decode(raw)
    def test_bulge_not_silently_flattened(self):
        raw=bytearray(self.raw());offset=len(raw)-P.POINT.size*2+24;struct.pack_into('<d',raw,offset,.25)
        with self.assertRaises(ValueError):P.decode(raw)
        self.assertTrue(P.decode(raw,allow_bulge_chords=True)['bulges'])
    def test_transient_pointer_ignored_only(self):
        raw=self.raw();changed=bytearray(raw);struct.pack_into('<d',changed,16+P.Track.points.offset,12345.)
        self.assertEqual(P.stable_bytes(raw),P.stable_bytes(changed))
        changed[16]=ord('X');self.assertNotEqual(P.stable_bytes(raw),P.stable_bytes(changed))
    def test_z_not_discarded_on_native_write(self):
        with self.assertRaises(ValueError):P.geometry_body('Z',LineString([(0,0,3),(1,1,4)]))
    def test_projected_coordinates_refused(self):
        with self.assertRaises(ValueError):P.geometry_body('Projected',LineString([(500000,2000000),(500100,2000000)]))
    def test_style_overflow_refused(self):
        with self.assertRaises(ValueError):P.geometry_body('Style',self.line,style={'alpha':101})
        with self.assertRaises(ValueError):P.geometry_body('Style',box(0,0,1,1),style={'width':256})

class FavoritesTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.transport=MemoryTransport();self.api=Favorites(self.transport,allowed_parents=[1],backup_dir=self.temp.name)
        self.group=self.api.create_group(1,'Synthetic');self.line=self.api.create_geometry(self.group['id'],'Boundary',LineString([(0,0),(1,0)]))
    def test_full_tree_paths(self):
        nodes=self.api.walk();self.assertEqual(nodes[-1]['path'],['Synthetic','Boundary'])
    def test_unauthorized_existing_id_refused(self):
        reader=Favorites(self.transport,backup_dir=self.temp.name)
        with self.assertRaises(PermissionError):reader.update_metadata(self.line['id'],name='X')
    def test_existing_requires_binding(self):
        api=Favorites(self.transport,allowed_ids=[self.line['id']],backup_dir=self.temp.name)
        with self.assertRaises(ValueError):api.update_metadata(self.line['id'],name='X')
    def test_metadata_and_backup(self):
        result=self.api.update_metadata(self.line['id'],name='Renamed',hidden=True)
        self.assertTrue(Path(result['backup']).is_file());self.assertEqual(result['object']['name'],'Renamed');self.assertEqual(result['object']['visibility']['show'],32)
    def test_stale_source_blocks(self):
        api=Favorites(self.transport,allowed_ids=[self.line['id']],backup_dir=self.temp.name);api.bind(self.line['id'])
        self.api.update_metadata(self.line['id'],name='Changed elsewhere')
        with self.assertRaises(ValueError):api.update_metadata(self.line['id'],name='Overwrite')
    def test_replace_geometry_readback(self):
        result=self.api.replace_geometry(self.line['id'],LineString([(0,0),(2,0)]));self.assertEqual(result['object']['parts'][0][-1],[2.,0.])
    def test_replace_preserves_visibility_and_native_metadata(self):
        self.api.update_metadata(self.line['id'],hidden=True)
        raw=bytearray(self.transport.store[self.line['id']]);native=P.Track.from_buffer(raw,16);native.custom1=765;self.transport.store[self.line['id']]=bytes(raw);self.api.bind(self.line['id'])
        self.api.replace_geometry(self.line['id'],LineString([(0,0),(2,0)]))
        after=P.Track.from_buffer_copy(self.transport.store[self.line['id']][16:]);self.assertEqual(after.show,32);self.assertEqual(after.custom1,765)
    def test_move_preserves_semantics(self):
        other=self.api.create_group(1,'Other');result=self.api.move([self.line['id']],other['id']);self.assertEqual(result[0]['object']['parent_id'],other['id'])
    def test_move_cycle_refused(self):
        child=self.api.create_group(self.group['id'],'Child')
        with self.assertRaises(ValueError):self.api.move([self.group['id']],child['id'])
    def test_delete_and_verify_membership(self):
        self.api.delete([self.line['id']]);self.assertEqual(self.api.list(self.group['id']),[])
    def test_scope_cannot_authorize_parent_implicitly(self):
        with self.assertRaises(PermissionError):self.api.create_group(999,'Outside')
    def test_source_equal_detects_style(self):
        a=self.api.get(self.line['id']);b=dict(a,native_style={'line':123})
        with self.assertRaises(ValueError):source_equal(a,b)
    def test_save_hash_and_independent_readback(self):
        db=Path(self.temp.name)/'synthetic.db';db.write_bytes(b'before')
        receipt=save_and_verify(self.api,db,lambda:MemoryTransport(self.transport.store),object_ids=[self.line['id']],action=lambda:db.write_bytes(b'after'),timeout=.01,poll=.001)
        self.assertTrue(receipt['database_changed']);self.assertTrue(receipt['independent_semantic_readback'])
    def test_unchanged_db_does_not_claim_fresh_persistence(self):
        db=Path(self.temp.name)/'synthetic.db';db.write_bytes(b'unchanged')
        receipt=save_and_verify(self.api,db,lambda:MemoryTransport(self.transport.store),object_ids=[self.line['id']],action=lambda:None,timeout=0)
        self.assertFalse(receipt['database_changed']);self.assertIn('not_fresh_persistence_proof',receipt['persistence_evidence'])
    def test_same_session_refused(self):
        db=Path(self.temp.name)/'synthetic.db';db.write_bytes(b'x')
        with self.assertRaises(ValueError):save_and_verify(self.api,db,lambda:self.transport,object_ids=[self.line['id']],action=lambda:None,timeout=0)

class GeometryTests(unittest.TestCase):
    crs='EPSG:32631'
    def test_crs_roundtrip(self):
        point=Point(3,45);local=G.reproject(point,4326,self.crs);self.assertLess(G.reproject(local,self.crs,4326).distance(point),1e-10)
    def test_metric_guard(self):
        for crs in (4326,3857,2263):
            with self.assertRaises(ValueError):G.buffer_m(LineString([(0,0),(1,1)]),10,crs=crs)
    def test_buffer_distance(self):
        line=LineString([(0,0),(100,0)]);result=G.buffer_m(line,7,crs=self.crs);self.assertAlmostEqual(result.area,1400)
    def test_union_clip_difference_hole_area(self):
        result,stats=G.overlay([box(0,0,10,10),box(5,0,15,10)],clip=box(0,0,12,10),subtract=box(3,3,5,5))
        self.assertAlmostEqual(result.area,116);self.assertEqual(len(result.interiors),1);self.assertEqual(stats['union_area'],150)
    def test_exact_dedup_preserves_opposite_banks(self):
        a=mapping(LineString([(0,0),(10,0)]));b=mapping(LineString([(0,1),(10,1)]))
        rows=[dict(id='a',role='left',geometry=a),dict(id='b',role='left',geometry=a),dict(id='c',role='right',geometry=b),dict(id='d',role='reference',geometry=a)]
        kept,duplicates=G.exact_deduplicate(rows);self.assertEqual(len(kept),3);self.assertEqual(len(duplicates),1)
    def test_dedup_requires_semantics(self):
        with self.assertRaises(ValueError):G.exact_deduplicate([dict(geometry=mapping(Point(0,0)))])
    def test_partial_version_preserves_uncovered(self):
        result,stats=G.replace_covered([box(0,0,10,10)],[box(5,0,10,12)],coverage=box(5,-1,11,13),crs=self.crs)
        self.assertAlmostEqual(result.area,110);self.assertTrue(stats['old_outside_coverage_preserved'])
    def test_partial_version_outside_coverage_refused(self):
        with self.assertRaises(ValueError):G.replace_covered([box(0,0,10,10)],[box(5,0,12,12)],coverage=box(5,0,10,12),crs=self.crs)
    def test_paired_road_closure(self):
        polygon,checks=G.close_road_pair(LineString([(0,0),(100,0)]),LineString([(100,4),(0,4)]),crs=self.crs,max_cap_distance=5)
        self.assertEqual(polygon.area,400);self.assertTrue(checks['source_boundaries_covered'])
    def test_unpaired_fragment_and_large_cap_refused(self):
        with self.assertRaises(ValueError):G.close_road_pair(LineString([(0,0),(100,0)]),LineString([(0,100),(100,100)]),crs=self.crs,max_cap_distance=5)
    def test_straight_endpoint_extension(self):
        result=G.extend_endpoint(LineString([(0,0),(10,0)]),LineString([(15,-10),(15,10)]),max_distance=8,crs=self.crs);self.assertEqual(result.coords[-1],(15.,0.))
    def test_remote_junction_refused(self):
        with self.assertRaises(ValueError):G.extend_endpoint(LineString([(0,0),(10,0)]),LineString([(100,-10),(100,10)]),max_distance=8,crs=self.crs)
    def test_clip_side_explicit(self):
        result=G.clip_side(box(0,0,10,10),LineString([(5,-1),(5,11)]),Point(2,5),crs=self.crs);self.assertEqual(result.area,50)
    def test_asymmetric_banks_and_separate_buffer(self):
        baseline=LineString([(0,0),(100,0)]);crest=G.asymmetric_corridor(baseline,[(0,10,20),(100,10,20)],crs=self.crs)
        self.assertEqual(crest.bounds,(0.,-20.,100.,10.));self.assertEqual(crest.area,3000)
        acquired=G.buffer_m(crest,5,crs=self.crs);self.assertEqual(acquired.bounds,(-5.,-25.,105.,15.))
    def test_profile_must_span_baseline(self):
        with self.assertRaises(ValueError):G.asymmetric_corridor(LineString([(0,0),(100,0)]),[(0,10,20),(50,10,20)],crs=self.crs)
    def test_geodesic_measure(self):
        values=G.measure(box(499950,4982900,500050,4983000),crs=self.crs);self.assertAlmostEqual(values['projected_area_m2'],10000);self.assertLess(abs(values['geodesic_area_m2']-10000),20)

class KmlTests(unittest.TestCase):
    def test_hierarchy_holes_and_z_roundtrip(self):
        polygon=Polygon([(0,0),(1,0),(1,1),(0,1),(0,0)],[[(.2,.2),(.2,.4),(.4,.4),(.4,.2),(.2,.2)]])
        rows=[dict(name='Synthetic polygon',path=['Group','Sub'],geometry=mapping(polygon),properties={'role':'range'}),dict(name='Z line',path=['Group'],geometry=mapping(LineString([(2,2,5),(3,3,6)])))]
        with TemporaryDirectory() as tmp:
            for suffix in ('.kml','.kmz'):
                path=Path(tmp)/('synthetic'+suffix);kml.write(path,rows);back=kml.read(path)
                self.assertEqual(back[0]['path'],['Group','Sub']);self.assertTrue(shape(back[0]['geometry']).equals(polygon));self.assertEqual(shape(back[1]['geometry']).coords[0][2],5)
    def test_existing_output_protected(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'keep.kml';path.write_text('original')
            with self.assertRaises(FileExistsError):kml.write(path,[])
            self.assertEqual(path.read_text(),'original')
    def test_projected_kml_output_refused(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):kml.write(Path(tmp)/'projected.kml',[dict(geometry=mapping(LineString([(500000,2000000),(500100,2000000)])))])
    def test_xml_entities_refused(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'bad.kml';path.write_text('<!DOCTYPE x [<!ENTITY y "x">]><kml/>')
            with self.assertRaises(ValueError):kml.read(path)
    def test_unsupported_placemark_not_silently_lost(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'unsupported.kml';path.write_text('<kml xmlns="http://www.opengis.net/kml/2.2"><Placemark><Model/></Placemark></kml>')
            with self.assertRaises(ValueError):kml.read(path)
    def test_network_link_not_fetched_or_silently_omitted(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'network.kml';path.write_text('<kml xmlns="http://www.opengis.net/kml/2.2"><NetworkLink/></kml>')
            with self.assertRaises(ValueError):kml.read(path)
    def test_mixed_multigeometry_refuses_unsupported_child(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'mixed.kml';path.write_text('<kml xmlns="http://www.opengis.net/kml/2.2"><Placemark><MultiGeometry><LineString><coordinates>0,0 1,1</coordinates></LineString><Model/></MultiGeometry></Placemark></kml>')
            with self.assertRaisesRegex(ValueError,'unsupported KML geometry: Model'):kml.read(path)
    def test_placemark_mixed_model_refuses_partial_import(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'mixed.kml';path.write_text('<kml xmlns="http://www.opengis.net/kml/2.2"><Placemark><LineString><coordinates>0,0 1,1</coordinates></LineString><Model/></Placemark></kml>')
            with self.assertRaises(ValueError):kml.read(path)
    def test_stylemap_normal_highlight_and_definitions_roundtrip(self):
        xml='<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Style id="line"><LineStyle><color>ff0000ff</color><width>2</width></LineStyle></Style><Style id="highlight"><LineStyle><color>ff00ff00</color><width>5</width></LineStyle></Style><StyleMap id="map"><Pair><key>normal</key><styleUrl>#line</styleUrl></Pair><Pair><key>highlight</key><styleUrl>#highlight</styleUrl></Pair></StyleMap><Placemark><styleUrl>#map</styleUrl><LineString><coordinates>0,0 1,1</coordinates></LineString></Placemark></Document></kml>'
        with TemporaryDirectory() as tmp:
            source=Path(tmp)/'source.kml';source.write_text(xml)
            rows=kml.read(source);self.assertEqual(len(rows[0]['style_definitions']),3)
            for suffix in ('.kml','.kmz'):
                output=Path(tmp)/('out'+suffix);kml.write(output,rows);back=kml.read(output)
                self.assertEqual(back[0]['style_url'],'#map');self.assertEqual(set(back[0]['style_definitions']),set(rows[0]['style_definitions']))
    def test_missing_local_style_reference_refused_on_read(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'missing.kml';path.write_text('<kml xmlns="http://www.opengis.net/kml/2.2"><Placemark><styleUrl>#missing</styleUrl><Point><coordinates>0,0</coordinates></Point></Placemark></kml>')
            with self.assertRaisesRegex(ValueError,'unresolved local'):kml.read(path)
    def test_missing_local_style_reference_refused_before_write(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'missing.kml'
            with self.assertRaisesRegex(ValueError,'unresolved local'):kml.write(path,[dict(style_url='#missing',geometry=mapping(Point(0,0)))])
            self.assertFalse(path.exists())
    def test_cyclic_stylemap_reference_refused(self):
        xml='<kml xmlns="http://www.opengis.net/kml/2.2"><Document><StyleMap id="a"><Pair><key>normal</key><styleUrl>#b</styleUrl></Pair></StyleMap><StyleMap id="b"><Pair><key>normal</key><styleUrl>#a</styleUrl></Pair></StyleMap><Placemark><styleUrl>#a</styleUrl><Point><coordinates>0,0</coordinates></Point></Placemark></Document></kml>'
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'cycle.kml';path.write_text(xml)
            with self.assertRaisesRegex(ValueError,'cyclic'):kml.read(path)

class MapTransport:
    window=10;app_id=2
    def __init__(self, *, missing=False,wrong_id=False,wrong_extent=False):self.missing=missing;self.wrong_id=wrong_id;self.wrong_extent=wrong_extent;self.sent=None
    def request(self,cmd,data=b'',**options):
        if cmd==20012:
            request=I.MapInfo.from_buffer_copy(data);assert request.real2==1
            result=I.MapInfo();result.level=10;result.maptype=99;result.real=0;result.real2=1;result.x=3.;result.y=45.;return 1,bytes(result)
        if cmd==20016:
            request=I.ScreenBounds.from_buffer_copy(data);assert request.request_type==1
            result=I.ScreenBounds();result.real=1;result.east=1.;result.north=1.;result.level=10;result.maptype=99;return 1,bytes(result)
        if cmd==20302:
            request=I.MapFile.from_buffer_copy(data[16:]);self.sent=request;assert request.sendreq==0
            result=I.MapFile.from_buffer_copy(bytes(request));result.id+=int(self.wrong_id);result.width=20;result.height=10;result.total=2;result.has=1 if self.missing else 2
            if self.wrong_extent:result.x1+=.001
            output=BytesIO();Image.new('RGB',(20,10),'green').save(output,format='PNG');raw=output.getvalue();result.nData=len(raw);result.bfile=False
            return 1,bytes(result)+raw
        raise AssertionError(cmd)

class ImageryTests(unittest.TestCase):
    def test_metadata_field_selection(self):
        fake=MapTransport();info=I.map_info(fake);self.assertEqual(info['level'],10);self.assertEqual((info['real'],info['real2'],info['x'],info['y']),(0,1,3.,45.));I.screen_bounds(fake)
    def test_screen_bounds_wrong_coordinate_flag_refused(self):
        class WrongCoordinates:
            def request(self,*args,**kwargs):return 1,bytes(I.ScreenBounds())
        with self.assertRaises(ValueError):I.screen_bounds(WrongCoordinates())
    def test_complete_cache_export(self):
        with TemporaryDirectory() as tmp:
            fake=MapTransport();receipt=I.export_cached_map(fake,Path(tmp)/'map.png',(0,0,1,1));self.assertFalse(receipt['network_requested']);self.assertTrue((Path(tmp)/'map.png').is_file());self.assertEqual(fake.sent.maptype,99)
    def test_partial_cache_refused(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):I.export_cached_map(MapTransport(missing=True),Path(tmp)/'map.png',(0,0,1,1))
    def test_wrong_request_id_refused(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):I.export_cached_map(MapTransport(wrong_id=True),Path(tmp)/'map.png',(0,0,1,1))
    def test_wrong_response_extent_refused(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):I.export_cached_map(MapTransport(wrong_extent=True),Path(tmp)/'map.png',(0,0,1,1))
    def test_crs_overlay_position(self):
        image=Image.new('RGB',(101,101),'black');result=I.draw_overlay(image,[LineString([(0,5),(10,5)])],(0,0,10,10),image_crs=32631,geometry_crs=32631,color=(255,0,0),width=1);self.assertEqual(result.getpixel((50,50)),(255,0,0));self.assertEqual(image.getpixel((50,50)),(0,0,0))
    def test_unfold_left_right_and_outside_unknown(self):
        image=Image.new('RGB',(101,101),'green');strip,meta=I.unfold_line(image,(0,-5,10,5),LineString([(0,0),(10,0)]),image_crs=32631,line_crs=32631,left_m=7,right_m=2,step_m=1,cross_step_m=1)
        self.assertEqual(meta['cross_offsets_m'][0],7);self.assertGreater(meta['outside_samples'],0);self.assertEqual(strip.getpixel((0,0)),(255,0,255))

class CliTests(unittest.TestCase):
    def module(self):
        spec=importlib.util.spec_from_file_location('ovital_cli_test',Path(__file__).resolve().parents[1]/'scripts/ovital_tool.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
    def test_calculate_real_cli(self):
        with TemporaryDirectory() as tmp:
            plan=Path(tmp)/'plan.json';result=Path(tmp)/'result.json'
            plan.write_text(json.dumps(dict(operation='road-pair',crs='EPSG:32631',geometries=[mapping(LineString([(0,0),(100,0)])),mapping(LineString([(0,4),(100,4)]))],max_cap_distance_m=5)),encoding='utf-8')
            with contextlib.redirect_stdout(StringIO()):self.assertEqual(self.module().main(['calculate',str(plan),'--output',str(result)]),0)
            row=json.loads(result.read_text('utf-8'));self.assertEqual(shape(row['geometry']).area,400)
    def test_native_requires_live_flag_before_connect(self):
        from types import SimpleNamespace
        with self.assertRaises(PermissionError):self.module().native({'action':'snapshot'},SimpleNamespace(allow_live=False))
    def test_write_requires_write_flag_before_connect(self):
        from types import SimpleNamespace
        with self.assertRaises(PermissionError):self.module().native({'action':'mutate'},SimpleNamespace(allow_live=True,allow_write=False))

class FiniteParameterTests(unittest.TestCase):
    crs='EPSG:32631'
    def values(self):return (float('nan'),float('inf'),float('-inf'))
    def test_road_cap_budget_rejects_all_nonfinite(self):
        for value in self.values():
            with self.subTest(value=value),self.assertRaises(ValueError):G.close_road_pair(LineString([(0,0),(100,0)]),LineString([(0,100),(100,100)]),crs=self.crs,max_cap_distance=value)
    def test_buffer_finite_gate_preserves_negative_erosion(self):
        self.assertEqual(G.buffer_m(box(0,0,10,10),-1,crs=self.crs).area,64)
        for value in self.values():
            with self.subTest(value=value),self.assertRaises(ValueError):G.buffer_m(box(0,0,10,10),value,crs=self.crs)
    def test_extension_budget_nonfinite(self):
        for value in self.values():
            with self.subTest(value=value),self.assertRaises(ValueError):G.extend_endpoint(LineString([(0,0),(10,0)]),LineString([(15,-5),(15,5)]),crs=self.crs,max_distance=value)
    def test_corridor_miter_and_step_nonfinite(self):
        line=LineString([(0,0),(100,0)]);profile=[(0,10,20),(100,10,20)]
        for name in ('miter_limit','sample_step'):
            for value in self.values():
                with self.subTest(name=name,value=value),self.assertRaises(ValueError):G.asymmetric_corridor(line,profile,crs=self.crs,**{name:value})
        for value in (0,-1):
            with self.assertRaises(ValueError):G.asymmetric_corridor(line,profile,crs=self.crs,sample_step=value)
    def test_profile_all_numeric_fields_nonfinite(self):
        for index in range(3):
            for value in self.values():
                profile=[[0,10,20],[100,10,20]];profile[0][index]=value
                with self.subTest(index=index,value=value),self.assertRaises(ValueError):G.asymmetric_corridor(LineString([(0,0),(100,0)]),profile,crs=self.crs)
    def test_geometry_nonfinite_including_z(self):
        for value in self.values():
            with self.subTest(value=value),self.assertRaises(ValueError):G.valid(Point(1,2,value))
        with self.assertRaises(ValueError):G.reproject(Point(1e6,45),4326,self.crs)
    def test_image_bounds_nonfinite_on_both_offline_paths(self):
        image=Image.new('RGB',(10,10));line=LineString([(0,0),(1,0)])
        for index in range(4):
            for value in self.values():
                bounds=[0.,-1.,2.,1.];bounds[index]=value
                with self.subTest(index=index,value=value),self.assertRaises(ValueError):I.draw_overlay(image,[line],bounds,image_crs=self.crs,geometry_crs=self.crs)
                with self.subTest(index=index,value=value),self.assertRaises(ValueError):I.unfold_line(image,bounds,line,image_crs=self.crs,line_crs=self.crs,left_m=1,right_m=1)
    def test_unfold_all_numeric_budgets_nonfinite(self):
        image=Image.new('RGB',(10,10));line=LineString([(0,0),(1,0)])
        for name in ('left_m','right_m','step_m','cross_step_m','max_pixels'):
            for value in self.values():
                kwargs=dict(left_m=1,right_m=1);kwargs[name]=value
                with self.subTest(name=name,value=value),self.assertRaises(ValueError):I.unfold_line(image,(0,-1,2,1),line,image_crs=self.crs,line_crs=self.crs,**kwargs)
    def test_unfold_checks_budget_before_allocation(self):
        with self.assertRaisesRegex(ValueError,'before allocation'):I.unfold_line(Image.new('RGB',(10,10)),(0,-1,100,1),LineString([(0,0),(100,0)]),image_crs=self.crs,line_crs=self.crs,left_m=1,right_m=1,step_m=1e-8,max_pixels=10)
    def test_export_level_type_timeout_and_bounds_nonfinite(self):
        with TemporaryDirectory() as tmp:
            for name in ('level','map_type','timeout'):
                for value in self.values():
                    with self.subTest(name=name,value=value),self.assertRaises(ValueError):I.export_cached_map(MapTransport(),Path(tmp)/'unused.png',(0,0,1,1),**{name:value})
            for index in range(4):
                for value in self.values():
                    bounds=[0.,0.,1.,1.];bounds[index]=value
                    with self.subTest(index=index,value=value),self.assertRaises(ValueError):I.export_cached_map(MapTransport(),Path(tmp)/'unused.png',bounds)
    def test_export_wrong_response_nan_extent_refused(self):
        class NonfiniteResponse(MapTransport):
            def request(self,cmd,data=b'',**options):
                code,payload=super().request(cmd,data,**options)
                if cmd==20302:
                    rep=I.MapFile.from_buffer_copy(payload);rep.x1=float('nan');payload=bytes(rep)+payload[C.sizeof(I.MapFile):]
                return code,payload
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):I.export_cached_map(NonfiniteResponse(),Path(tmp)/'unused.png',(0,0,1,1))
            self.assertFalse((Path(tmp)/'unused.png').exists())
    def test_cli_json_rejects_nan_infinity_and_overflow_numeric(self):
        module=CliTests().module()
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'bad.json'
            for value in ('NaN','Infinity','-Infinity','1e999','-1e999'):
                path.write_text('{"distance":'+value+'}',encoding='utf-8')
                with self.subTest(value=value),self.assertRaises(ValueError):module.load(path)
    def test_native_style_all_numeric_fields_nonfinite(self):
        for name in ('line','area','width','alpha','area_alpha','line_type','closed','outer_count'):
            for value in self.values():
                with self.subTest(name=name,value=value),self.assertRaises(ValueError):P.geometry_body('Synthetic',box(0,0,1,1),style={name:value})
    def test_kml_resource_budget_nonfinite(self):
        for value in self.values():
            with self.subTest(value=value),self.assertRaises(ValueError):kml.read('never-opened.kml',max_xml_bytes=value)
    def test_native_timeouts_ids_and_header_nonfinite(self):
        from ovital.session import WinMessageTransport
        for value in self.values():
            for name in ('timeout','response_timeout'):
                with self.subTest(name=name,value=value),self.assertRaises(ValueError):WinMessageTransport(**{name:value})
            with self.assertRaises(ValueError):P.header(parent=value)
            with self.assertRaises(ValueError):Favorites(MemoryTransport(),allowed_ids=[value])
            with self.assertRaises(ValueError):Favorites(MemoryTransport()).walk(max_objects=value)
    def test_save_wait_parameters_nonfinite(self):
        for name in ('timeout','poll'):
            for value in self.values():
                with self.subTest(name=name,value=value),self.assertRaises(ValueError):save_and_verify(Favorites(MemoryTransport()),'never-opened',lambda:None,object_ids=[1],**{name:value})

if __name__=='__main__':unittest.main(verbosity=2)
