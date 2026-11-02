"""Offline KML/KMZ roundtrip, hierarchy, holes, multi geometry and inline style."""
from pathlib import Path
import math
import xml.etree.ElementTree as ET
import zipfile
from shapely.geometry import Point,LineString,Polygon,GeometryCollection,shape,mapping
from .validation import finite_number

K='http://www.opengis.net/kml/2.2';N={'k':K};ET.register_namespace('',K)

def _child(parent,name,text=None):
    element=ET.SubElement(parent,'{'+K+'}'+name)
    if text is not None:element.text=str(text)
    return element

def _coordinates(element):
    if element is None or not element.text:raise ValueError('missing KML coordinates')
    rows=[tuple(map(float,item.split(','))) for item in element.text.split()]
    if any(len(row) not in (2,3) for row in rows):raise ValueError('coordinate dimension must be 2 or 3')
    for row in rows:
        if not all(math.isfinite(v) for v in row) or not -180<=row[0]<=180 or not -90<=row[1]<=90:raise ValueError('KML requires finite WGS84 longitude/latitude, not projected coordinates')
    return rows

def _geometry(element):
    name=element.tag.split('}')[-1]
    if name=='Point':
        points=_coordinates(element.find('k:coordinates',N))
        if len(points)!=1:raise ValueError('KML Point needs one coordinate')
        return Point(points[0])
    if name in ('LineString','LinearRing'):return LineString(_coordinates(element.find('k:coordinates',N)))
    if name=='Polygon':
        outer=_coordinates(element.find('k:outerBoundaryIs/k:LinearRing/k:coordinates',N))
        holes=[_coordinates(e) for e in element.findall('k:innerBoundaryIs/k:LinearRing/k:coordinates',N)]
        if any(r[0]!=r[-1] for r in [outer]+holes):raise ValueError('KML polygon rings must close explicitly')
        return Polygon(outer,holes)
    if name=='MultiGeometry':
        # Every child is a geometry, not optional metadata. Never filter an
        # unsupported Model/gx:Track away while retaining the supported part.
        if not len(element):raise ValueError('empty MultiGeometry')
        return GeometryCollection([_geometry(e) for e in element])
    raise ValueError('unsupported KML geometry: '+name)

def _style_registry(elements):
    registry={}
    for element in elements:
        identifier=element.get('id')
        if not identifier:continue
        if element.tag not in ('{'+K+'}Style','{'+K+'}StyleMap'):raise ValueError('unsupported style definition')
        if identifier in registry and ET.tostring(registry[identifier])!=ET.tostring(element):raise ValueError('conflicting KML style IDs')
        registry[identifier]=element
    return registry

def _style_references(element):
    return [(node.text or '').strip() for node in element.findall('.//k:styleUrl',N)]

def _style_closure(references,registry):
    """Retain complete StyleMap chains (normal AND highlight); never flatten."""
    found={};active=set()
    def resolve(reference):
        if not reference.startswith('#'):return  # External URL is retained, never fetched.
        identifier=reference[1:]
        if not identifier or identifier not in registry:raise ValueError('unresolved local KML styleUrl: '+reference)
        if identifier in active:raise ValueError('cyclic KML style references')
        if identifier in found:return
        active.add(identifier);element=registry[identifier]
        for nested in _style_references(element):resolve(nested)
        active.remove(identifier);found[identifier]=ET.tostring(element,encoding='unicode')
    for reference in references:resolve(reference)
    return list(found.values())

def read(path, *, max_xml_bytes=32*1024*1024):
    max_xml_bytes=finite_number(max_xml_bytes,'KML XML byte budget',positive=True,integer=True)
    path=Path(path)
    if path.suffix.lower()=='.kmz':
        with zipfile.ZipFile(path) as archive:
            candidates=[i for i in archive.infolist() if i.filename.lower().endswith('.kml')]
            main=[i for i in candidates if i.filename.lower()=='doc.kml']
            if not main and len(candidates)!=1:raise ValueError('ambiguous KMZ document; select/export the intended KML')
            selected=(main or candidates)[0]
            if selected.file_size>max_xml_bytes:raise ValueError('KML size bound exceeded')
            data=archive.read(selected)
    else:
        if path.stat().st_size>max_xml_bytes:raise ValueError('KML size bound exceeded')
        data=path.read_bytes()
    scan=data.replace(b'\0',b'').upper()
    if b'<!DOCTYPE' in scan or b'<!ENTITY' in scan:raise ValueError('DTD/entity declarations are not accepted')
    root=ET.fromstring(data);features=[]
    styles=_style_registry(root.findall('.//k:Style',N)+root.findall('.//k:StyleMap',N))
    def visit(element,folders):
        typ=element.tag.split('}')[-1]
        if typ in ('NetworkLink','GroundOverlay','PhotoOverlay','ScreenOverlay'):raise ValueError('linked/overlay content is not local vector geometry; export it separately, no remote fetch is performed')
        if typ=='Folder':folders=folders+[element.findtext('k:name','',N)]
        if typ=='Placemark':
            if any(e.tag.split('}')[-1] in ('Model','Track','MultiTrack') for e in element):raise ValueError('unsupported Placemark geometry; refusing partial import')
            geometries=[_geometry(e) for e in element if e.tag.split('}')[-1] in ('Point','LineString','LinearRing','Polygon','MultiGeometry')]
            if not geometries:raise ValueError('Placemark has no supported local geometry; refusing silent feature loss')
            geometry=geometries[0] if len(geometries)==1 else GeometryCollection(geometries)
            if not geometry.is_valid:raise ValueError('invalid KML geometry; no implicit repair')
            inline=[e for e in element if e.tag in ('{'+K+'}Style','{'+K+'}StyleMap')]
            if len(inline)>1:raise ValueError('ambiguous inline KML style selector')
            url=element.findtext('k:styleUrl','',N).strip();references=[url]
            if inline:references+=_style_references(inline[0])
            definitions=_style_closure(references,styles)
            style_xml=ET.tostring(inline[0],encoding='unicode') if inline else None
            features.append(dict(id=element.get('id'),name=element.findtext('k:name','',N),path=list(folders),description=element.findtext('k:description','',N),visibility=element.findtext('k:visibility','1',N),geometry=mapping(geometry),style_xml=style_xml,style_url=url,style_definitions=definitions,properties={e.get('name'):e.findtext('k:value','',N) for e in element.findall('k:ExtendedData/k:Data',N)}))
        else:
            for child in element:visit(child,folders)
    visit(root,[]);return features

def _write_geometry(parent,geometry):
    typ=geometry.geom_type
    def coords(points):
        points=list(points)
        for point in points:
            if not all(math.isfinite(v) for v in point) or not -180<=point[0]<=180 or not -90<=point[1]<=90:raise ValueError('reproject explicitly to WGS84 before KML output; projected/non-finite coordinates refused')
        return ' '.join(','.join(format(float(v),'.15g') for v in point) for point in points)
    if typ=='Point':_child(_child(parent,'Point'),'coordinates',coords(geometry.coords))
    elif typ in ('LineString','LinearRing'):_child(_child(parent,'LineString'),'coordinates',coords(geometry.coords))
    elif typ=='Polygon':
        element=_child(parent,'Polygon')
        for i,ring in enumerate([geometry.exterior]+list(geometry.interiors)):_child(_child(_child(element,'outerBoundaryIs' if not i else 'innerBoundaryIs'),'LinearRing'),'coordinates',coords(ring.coords))
    elif hasattr(geometry,'geoms'):
        element=_child(parent,'MultiGeometry')
        for part in geometry.geoms:_write_geometry(element,part)
    else:raise ValueError('unsupported output geometry')

def write(path,features, *, title='GIS export',overwrite=False):
    path=Path(path)
    if path.exists() and not overwrite:raise FileExistsError(path)
    root=ET.Element('{'+K+'}kml');doc=_child(root,'Document');_child(doc,'name',title);folders={():doc}
    features=list(features)
    definitions=_style_registry([ET.fromstring(xml) for feature in features for xml in feature.get('style_definitions',[])])
    references=[]
    for feature in features:
        references.append(str(feature.get('style_url') or ''))
        if feature.get('style_xml'):
            inline=ET.fromstring(feature['style_xml'])
            if inline.tag not in ('{'+K+'}Style','{'+K+'}StyleMap'):raise ValueError('unsupported inline style selector')
            references+=_style_references(inline)
    # Fail before opening output if user-supplied JSON contains dangling #refs.
    for xml in _style_closure(references,definitions):doc.append(ET.fromstring(xml))
    for feature in features:
        key=()
        for part in feature.get('path',[]):
            previous=key;key+=(str(part),)
            if key not in folders:folders[key]=_child(folders[previous],'Folder');_child(folders[key],'name',part)
        placemark=_child(folders[key],'Placemark')
        # KML XML IDs are optional. Preserve only explicit input IDs, never native IDs by default.
        if feature.get('id') is not None:placemark.set('id',str(feature['id']))
        for name in ('name','description','visibility'):
            if name in feature:_child(placemark,name,feature[name])
        if feature.get('style_url'):_child(placemark,'styleUrl',feature['style_url'])
        if feature.get('style_xml'):placemark.append(ET.fromstring(feature['style_xml']))
        data=_child(placemark,'ExtendedData')
        for name,value in feature.get('properties',{}).items():entry=_child(data,'Data');entry.set('name',str(name));_child(entry,'value',value)
        geometry=shape(feature['geometry'])
        if geometry.is_empty or not geometry.is_valid:raise ValueError('output geometry invalid/empty')
        _write_geometry(placemark,geometry)
    xml=ET.tostring(root,encoding='utf-8',xml_declaration=True)
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.suffix.lower()=='.kmz':
        with zipfile.ZipFile(path,'w' if overwrite else 'x',compression=zipfile.ZIP_DEFLATED) as archive:archive.writestr('doc.kml',xml)
    else:
        with path.open('wb' if overwrite else 'xb') as stream:stream.write(xml)
    return dict(path=str(path),features=len(features),xml_bytes=len(xml))
