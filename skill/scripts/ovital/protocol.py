"""API 103 serialized objects, derived from OMapApi header and proven adapters.

No DLL loading or live application contact. Windows default 8-byte ABI alignment.
Real-coordinate flag 1 means API CGCS2000 geographic, not a precision guarantee
that it is identical to WGS84. Bulges are retained and rejected for linear GIS
conversion unless the caller explicitly accepts straight-chord approximation.
"""
import ctypes as C
import hashlib
import json
import math
import struct
from .validation import finite_number

API_VERSION=103
HEADER=struct.Struct('<HBBIIHBB')
POINT=struct.Struct('<ddiid i 4x d')
u8,u16,u32,i32=C.c_uint8,C.c_uint16,C.c_uint32,C.c_int32

class View3D(C.LittleEndianStructure):
    _pack_=8
    _fields_=[('eye',i32),('alt',i32),('lat',C.c_float),('lng',C.c_float),('rx',C.c_float),('rz',C.c_float),('flags',u8*8),('line',u32),('ret0',u32),('ret',u32*10)]

class Draw(C.LittleEndianStructure):
    _pack_=8
    _fields_=[('line',u32),('circle',u32),('width',u16),('circle_width',u8),('alpha',u8),('show_type',u8),('line_type',u8),('close',u8)]

class Track(C.LittleEndianStructure):
    _pack_=8
    _fields_=[('name',C.c_char*100),('comment',C.c_double),('track_type',u8),('overlay',u8),('real',u8),('show',u8),('show_bak',u8),('show_max',u8),('show_name',u8),('edit',u8),('alt_color',u32),('custom',u8*4),('custom1',i32),('custom2',i32),('points',C.c_double),('count',i32),('draw',Draw),('line_scale',C.c_float),('cad_width',u8),('spline',u8),('view',View3D),('ret',u32*20)]

class Shape(C.LittleEndianStructure):
    _pack_=8
    _fields_=[('points',C.c_double),('count',i32),('name',C.c_char*100),('show_flag',u8),('overlay',u8),('real',u8),('comment',C.c_double),('start_angle',C.c_double),('end_angle',C.c_double),('line_width',u8),('line_alpha',u8),('line_type',u8),('area_alpha',u8),('edit',u8),('show',u8),('show_bak',u8),('show_max',u8),('fill',u8),('fill_sub',u8),('outer_count',u16),('line',u32),('area',u32),('pattern',u32),('fill_color',u32),('fill_rotate',C.c_float),('fill_scale',C.c_float),('fill_x',C.c_float),('fill_y',C.c_float),('cad_width',u8),('view',View3D),('ret',u32*20)]

class Group(C.LittleEndianStructure):
    _pack_=8
    _fields_=[('children',i32),('items',i32),('mem',i32),('name',C.c_char*100),('merge',u8),('group',u8),('autoload',u8),('show',u8),('show_bak',u8),('show_max',u8),('share',u8),('readonly',u8),('nohot',u8),('relate',u8),('bind',u32),('bind_check',u32),('link',u8),('link_auto',u8),('link_name',C.c_char*256),('overlays',u32),('line',u32),('line_type',u8),('line_width',u8),('line_alpha',u8),('reserved',u8),('ret',u32*8)]

STRUCTURES={8:Track,13:Shape,30:Group}

def header(typ=0,parent=0,oid=0,coordinate_flag=1,position=0):
    typ=finite_number(typ,'native object type',minimum=0,maximum=255,integer=True)
    parent=finite_number(parent,'native parent ID',minimum=0,maximum=0xffffffff,integer=True)
    oid=finite_number(oid,'native object ID',minimum=0,maximum=0xffffffff,integer=True)
    coordinate_flag=finite_number(coordinate_flag,'coordinate flag',minimum=0,maximum=2,integer=True)
    position=finite_number(position,'native position',minimum=0,maximum=65535,integer=True)
    return HEADER.pack(API_VERSION,typ,coordinate_flag,parent,oid,position,0,0)

def parse_header(raw):
    if len(raw)<HEADER.size:raise ValueError('truncated API header')
    version,typ,real,parent,oid,pos,tree,res=HEADER.unpack_from(raw)
    if version!=API_VERSION:raise ValueError('unsupported serialized API version')
    return dict(version=version,type=typ,coordinate_flag=real,parent_id=parent,id=oid,position=pos)

def name_bytes(name):
    value=str(name).encode('utf-8')
    if b'\0' in value or len(value)>=100:raise ValueError('name must be less than 100 UTF-8 bytes without NUL')
    return value

def object_name(raw):
    typ=parse_header(raw)['type'];cls=STRUCTURES.get(typ)
    if cls is not None:
        if len(raw)<16+C.sizeof(cls):raise ValueError('truncated object structure')
        return bytes(cls.from_buffer_copy(raw[16:]).name).split(b'\0',1)[0].decode('utf-8')
    if typ==7 and len(raw)>=144:return raw[44:144].split(b'\0',1)[0].decode('utf-8')
    raise ValueError('unsupported native name layout')

def stable_bytes(raw):
    """Ignore only pointer slots, not names, geometry, parents or style."""
    cls=STRUCTURES.get(parse_header(raw)['type']);data=bytearray(raw)
    if cls:
        for field in ('points','comment'):
            if hasattr(cls,field):
                offset=16+getattr(cls,field).offset;data[offset:offset+8]=b'\0'*8
    return bytes(data)

def decode(raw, *, allow_bulge_chords=False):
    from shapely.geometry import Point,LineString,Polygon,MultiPolygon,GeometryCollection,mapping
    result=parse_header(raw);typ=result['type'];body=raw[16:]
    result['name']=object_name(raw)
    if typ==7:
        lat,lng=struct.unpack_from('<dd',body,8)
        result.update(parts=[[[lng,lat]]],geometry=mapping(Point(lng,lat)))
        return result
    if typ not in STRUCTURES:raise ValueError('unsupported native object type')
    cls=STRUCTURES[typ]
    if len(body)<C.sizeof(cls):raise ValueError('truncated native structure')
    value=cls.from_buffer_copy(body)
    result['visibility']={n:int(getattr(value,n)) for n in ('show','show_bak','show_max')}
    if typ==30:
        if len(body)!=C.sizeof(Group):raise ValueError('unexpected group size')
        return result
    if value.real!=1:raise ValueError('geographic real-coordinate flag 1 required')
    size=C.sizeof(cls)
    if len(body)<size+4:raise ValueError('missing comment length')
    length=struct.unpack_from('<i',body,size)[0]
    if length<0 or size+4+length>len(body):raise ValueError('invalid comment length')
    start=(size+4+length+7)//8*8
    if value.count<0 or len(body)!=start+value.count*POINT.size:raise ValueError('point count/payload mismatch')
    parts=[];part=[];point_metadata=[];bulges=[]
    for index in range(value.count):
        lat,lng,tm,alt,bulge,cad,ext=POINT.unpack_from(body,start+index*POINT.size)
        if not all(math.isfinite(x) for x in (lat,lng,bulge,ext)):raise ValueError('non-finite coordinate')
        if not -180<=lng<=180 or not -90<=lat<=90:raise ValueError('coordinate outside geographic range')
        if (alt>>16)&1 and part:parts.append(part);part=[]
        part.append([lng,lat]);point_metadata.append(dict(time=tm,alt_flags=alt,bulge=bulge,cad=cad,ext=ext))
        if abs(bulge)>1e-10:bulges.append([index,bulge])
    if part:parts.append(part)
    result.update(parts=parts,point_count=value.count,point_metadata=point_metadata,bulges=bulges,comment=body[size+4:size+4+length].decode('utf-8'),coordinate_reference='API real=1 CGCS2000 geographic')
    if (bulges or (typ==8 and value.spline)) and not allow_bulge_chords:raise ValueError('curves/splines require explicit chord approximation or a curve-aware adapter')
    if typ==8:
        lines=[LineString(p) if len(p)>1 else Point(p[0]) for p in parts if p]
        geom=lines[0] if len(lines)==1 else GeometryCollection(lines)
        result['native_style']=dict(line=int(value.draw.line),width=int(value.draw.width),alpha=int(value.draw.alpha),line_type=int(value.draw.line_type),closed=bool(value.draw.close))
    else:
        rings=[p for p in parts if len(p)>=4 and p[0]==p[-1]]
        if len(rings)!=len(parts) or not rings:raise ValueError('polygon rings must be explicitly closed')
        outer=value.outer_count or len(rings)
        if outer>len(rings):raise ValueError('invalid outer ring count')
        shells=[Polygon(r) for r in rings[:outer]];holes=rings[outer:];assigned=[[] for _ in shells]
        for ring in holes:
            matches=[i for i,p in enumerate(shells) if p.covers(Polygon(ring))]
            if len(matches)!=1:raise ValueError('ambiguous or orphan polygon hole')
            assigned[matches[0]].append(ring)
        polygons=[Polygon(rings[i],assigned[i]) for i in range(outer)]
        geom=polygons[0] if len(polygons)==1 else MultiPolygon(polygons)
        result['native_style']=dict(line=int(value.line),width=int(value.line_width),alpha=int(value.line_alpha),line_type=int(value.line_type),area=int(value.area),area_alpha=int(value.area_alpha),outer_count=int(value.outer_count),closed=True)
    if not geom.is_valid:raise ValueError('invalid native geometry; inspect rather than silently repair')
    result['geometry']=mapping(geom)
    return result

def group_body(name, *, hidden=False):
    value=Group();value.name=name_bytes(name);value.autoload=1;value.show=32 if hidden else 0
    return bytes(value)

def geometry_body(name,geometry, *, comment='',style=None,coordinate_flag=1):
    """Serialize one line/multiline or one Polygon; multipart polygons use separate objects."""
    from shapely.geometry import shape
    geom=shape(geometry) if isinstance(geometry,dict) else geometry
    if geom.is_empty or not geom.is_valid:raise ValueError('nonempty valid geometry required')
    style=style or {}
    limits={'line':0xffffffff,'area':0xffffffff,'width':65535 if geom.geom_type in ('LineString','MultiLineString') else 255,'alpha':100,'area_alpha':100,'line_type':255}
    if set(style)-set(limits)-{'closed','outer_count'}:raise ValueError('unsupported native style field')
    for key,limit in limits.items():
        if key in style and (type(style[key]) is not int or not 0<=style[key]<=limit):raise ValueError('native style value outside documented integer range: '+key)
    if 'closed' in style and type(style['closed']) is not bool:raise ValueError('closed style flag must be boolean')
    if 'outer_count' in style:finite_number(style['outer_count'],'native outer ring count',minimum=0,maximum=65535,integer=True)
    if coordinate_flag!=1:raise ValueError('only real-coordinate geographic output is supported')
    if geom.geom_type in ('LineString','MultiLineString'):
        typ=8;value=Track();parts=[list(g.coords) for g in (geom.geoms if geom.geom_type=='MultiLineString' else [geom])]
        value.draw.line=style.get('line',0xff0000);value.draw.width=style.get('width',30);value.draw.alpha=style.get('alpha',100);value.draw.close=bool(style.get('closed',False));value.draw.line_type=style.get('line_type',0)
    elif geom.geom_type=='Polygon':
        typ=13;value=Shape();parts=[list(geom.exterior.coords)]+[list(r.coords) for r in geom.interiors]
        value.outer_count=1;value.line=style.get('line',0xff0000);value.line_width=style.get('width',2);value.line_alpha=style.get('alpha',100);value.area=style.get('area',0xff0000);value.area_alpha=style.get('area_alpha',20);value.line_type=style.get('line_type',0)
    else:raise ValueError('native writer supports LineString/MultiLineString/Polygon')
    for ring in parts:
        for point in ring:
            x,y=point[:2]
            if len(point)!=2:raise ValueError('Z values cannot silently become API flags/height')
            if not math.isfinite(x) or not math.isfinite(y) or not -180<=x<=180 or not -90<=y<=90:raise ValueError('expected geographic longitude/latitude')
    value.name=name_bytes(name);value.real=1;value.count=sum(map(len,parts));text=str(comment).encode('utf-8')
    body=bytes(value)+struct.pack('<i',len(text))+text;body+=b'\0'*(-len(body)%8)
    for i,part in enumerate(parts):
        for j,(lng,lat) in enumerate(part):body+=POINT.pack(lat,lng,0,(1<<16) if i and not j else 0,0.,0,0.)
    # Validate our own bytes using the exact reader before sending.
    decode(header(typ)+body)
    return typ,body

def semantic_signature(record):
    keys=('id','type','parent_id','name','parts','comment','native_style','visibility','point_metadata')
    return hashlib.sha256(json.dumps({k:record.get(k) for k in keys},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
