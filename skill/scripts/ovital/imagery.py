"""Official map metadata/cache export plus CRS-aware overlays and line strips.

No downloads: bSendReq stays 0. API/imagery entitlement is checked by Ovital.
Map export is a filesystem effect; call only for an explicit output file.
"""
import ctypes as C
from io import BytesIO
import math
from pathlib import Path
from uuid import uuid4
from PIL import Image,ImageDraw
from pyproj import Transformer
from .protocol import header
from .geometry import reproject,require_metric

class MapInfo(C.LittleEndianStructure):
    _pack_=8
    _fields_=[('lng',C.c_double),('lat',C.c_double),('real',C.c_ubyte),('x',C.c_double),('y',C.c_double),('real2',C.c_ubyte),('level',C.c_int32),('maptype',C.c_int32),('mx',C.c_int32),('my',C.c_int32),('retcode',C.c_int32),('image',C.c_int32),('reserved',C.c_uint32*9)]

class ScreenBounds(C.LittleEndianStructure):
    _pack_=8
    _fields_=[('version',C.c_uint8),('request_type',C.c_uint8),('real',C.c_uint8),('reserved',C.c_uint8),('level',C.c_int32),('maptype',C.c_int32),('center_x',C.c_double),('center_y',C.c_double),('west',C.c_double),('north',C.c_double),('east',C.c_double),('south',C.c_double),('ret',C.c_int64*10)]

class MapFile(C.LittleEndianStructure):
    _pack_=8
    _fields_=[('id',C.c_int64),('value',C.c_int64),('flag',C.c_int32),('retcode',C.c_int32),('file',C.c_char*256),('bfile',C.c_bool),('data',C.c_uint64),('nData',C.c_int32),('x1',C.c_double),('y1',C.c_double),('x2',C.c_double),('y2',C.c_double),('real',C.c_ubyte),('level',C.c_int32),('maptype',C.c_int32),('width',C.c_uint32),('height',C.c_uint32),('hwnd',C.c_uint64),('total',C.c_int32),('has',C.c_int32),('postcount',C.c_int32),('tmp',C.c_int32),('sendreq',C.c_ubyte),('image',C.c_ubyte),('center',C.c_ubyte),('retuse',C.c_ubyte*2),('app',C.c_int32),('res',C.c_int32*18)]

def map_info(transport):
    request=MapInfo();request.real2=1  # SDK GetWinMapInfo sets bReal2, not bReal.
    _,payload=transport.request(20012,bytes(request))
    if len(payload)!=C.sizeof(MapInfo):raise ValueError('unexpected map-info ABI size')
    result=MapInfo.from_buffer_copy(payload)
    if result.retcode<0:raise RuntimeError('map-info rejected')
    # Preserve both coordinate groups and flags: SDK bReal2 controls x/y, not
    # necessarily the primary lng/lat pair. Never relabel GCJ02 as CGCS2000.
    return {key:getattr(result,key) for key in ('lng','lat','real','x','y','real2','level','maptype','image')}

def screen_bounds(transport):
    request=ScreenBounds();request.request_type=1  # SDK bReqType selects geographic coordinates.
    _,payload=transport.request(20016,bytes(request))
    if len(payload)!=C.sizeof(ScreenBounds):raise ValueError('unexpected screen-bounds ABI size')
    result=ScreenBounds.from_buffer_copy(payload)
    if result.real!=1:raise ValueError('requested CGCS2000 geographic screen bounds were not returned')
    return {key:getattr(result,key) for key in ('west','south','east','north','level','maptype','real')}

def export_cached_map(transport,path,bounds, *, level=None,map_type=None,timeout=30.):
    """Bounds=(west,south,east,north), API real=1 geographic. No map navigation."""
    path=Path(path).resolve()
    if path.exists():raise FileExistsError('map export never overwrites an existing file')
    west,south,east,north=map(float,bounds)
    if not (-180<=west<east<=180 and -90<=south<north<=90):raise ValueError('valid geographic extent required; split dateline exports')
    if level is None or map_type is None:
        current=map_info(transport)
        if level is None:level=current['level']
        if map_type is None:map_type=current['maptype']
    encoded=str(path).encode('utf-8')
    if len(encoded)>=256:raise ValueError('API filename must be under 256 UTF-8 bytes')
    path.parent.mkdir(parents=True,exist_ok=True)
    req=MapFile();req.id=uuid4().int&0x7fffffffffffffff;req.file=encoded;req.x1=west;req.y1=north;req.x2=east;req.y2=south;req.real=1;req.level=int(level);req.maptype=int(map_type);req.hwnd=transport.window;req.app=transport.app_id;req.sendreq=0;req.image=0;req.flag=1
    _,payload=transport.request(20302,header()+bytes(req),response_timeout=timeout)
    # SDK callbacks normally have no opt header. Accept an explicit valid header too.
    candidates=[]
    for offset in (0,16):
        if len(payload)>=offset+C.sizeof(MapFile):
            rep=MapFile.from_buffer_copy(payload[offset:])
            if rep.id==req.id:candidates.append((rep,offset))
    if len(candidates)!=1:raise ValueError('map callback does not uniquely match this request ID')
    rep,offset=candidates[0]
    if rep.retcode<0 or rep.total<=0 or rep.has!=rep.total or not rep.width or not rep.height:raise RuntimeError('cached imagery incomplete/rejected; no download or automatic retry')
    if rep.real!=req.real or rep.level!=req.level or rep.maptype!=req.maptype:raise ValueError('map response CRS/source/level mismatch')
    if any(abs(getattr(rep,key)-getattr(req,key))>1e-9 for key in ('x1','y1','x2','y2')):raise ValueError('map response extent differs from requested georeferencing')
    if rep.bfile:
        response_file=bytes(rep.file).split(b'\0',1)[0].decode('utf-8')
        if Path(response_file).resolve()!=path or not path.is_file():raise ValueError('map file identity mismatch')
    else:
        raw=payload[offset+C.sizeof(MapFile):]
        if rep.nData<=0 or len(raw)!=rep.nData:raise ValueError('missing inline image bytes; remote pointers are never dereferenced')
        with path.open('xb') as output:output.write(raw)
    with Image.open(path) as image:
        if image.size!=(rep.width,rep.height):raise ValueError('map dimensions differ from API receipt')
        image.verify()
    return dict(path=str(path),bounds=list(bounds),api_coordinate_flag=1,level=req.level,map_type=req.maptype,tiles_total=rep.total,tiles_present=rep.has,width=rep.width,height=rep.height,network_requested=False,precision='cached imagery; not survey/DEM accuracy')

def draw_overlay(image,geometries,bounds, *, image_crs,geometry_crs,color=(255,0,255),width=2):
    """Return a copy. bounds are exact image CRS extent, not a guessed screenshot."""
    image=image.copy().convert('RGB');draw=ImageDraw.Draw(image);west,south,east,north=bounds
    if not west<east or not south<north:raise ValueError('positive image extent required')
    def pixel(point):return ((point[0]-west)/(east-west)*(image.width-1),(north-point[1])/(north-south)*(image.height-1))
    def render(geometry):
        if geometry.geom_type=='Point':
            x,y=pixel(geometry.coords[0]);draw.ellipse((x-3,y-3,x+3,y+3),outline=color,width=width)
        elif geometry.geom_type in ('LineString','LinearRing'):draw.line([pixel(p) for p in geometry.coords],fill=color,width=width)
        elif geometry.geom_type=='Polygon':
            render(geometry.exterior)
            for ring in geometry.interiors:render(ring)
        elif hasattr(geometry,'geoms'):
            for part in geometry.geoms:render(part)
        else:raise ValueError('unsupported overlay geometry')
    for geometry in geometries:render(reproject(geometry,geometry_crs,image_crs))
    return image

def unfold_line(image,bounds,line, *, image_crs,line_crs,left_m,right_m,step_m=1.,cross_step_m=1.,max_pixels=20000000):
    """Along-line strip; image top is travel-left, bottom travel-right.

    Out-of-image samples are visibly magenta, not clipped to fake edge evidence.
    Result is an inspection aid, not resampled survey-quality imagery.
    """
    import numpy as np
    require_metric(line_crs)
    if line.geom_type!='LineString' or line.has_z or line.length<=0 or min(step_m,cross_step_m)<=0 or min(left_m,right_m)<0:raise ValueError('valid 2D metric line, explicit positive sampling and nonnegative widths required')
    stations=np.linspace(0,line.length,max(2,int(math.ceil(line.length/step_m))+1));distances=np.arange(left_m,-right_m-cross_step_m*.5,-cross_step_m)
    if len(stations)*len(distances)>max_pixels:raise ValueError('strip pixel budget exceeded')
    q=np.array([line.interpolate(float(s)).coords[0] for s in stations]);epsilon=min(step_m,1.)
    tangent=np.array([np.array(line.interpolate(min(line.length,float(s)+epsilon)).coords[0])-np.array(line.interpolate(max(0.,float(s)-epsilon)).coords[0]) for s in stations]);norm=np.hypot(*tangent.T)
    if (norm<1e-12).any():raise ValueError('undefined line direction')
    nx=-tangent[:,1]/norm;ny=tangent[:,0]/norm;x=q[None,:,0]+distances[:,None]*nx;y=q[None,:,1]+distances[:,None]*ny
    xx,yy=Transformer.from_crs(line_crs,image_crs,always_xy=True).transform(x,y);west,south,east,north=bounds
    if not west<east or not south<north:raise ValueError('positive image extent required')
    source=np.asarray(image.convert('RGB'));px=np.rint((xx-west)/(east-west)*(image.width-1)).astype(int);py=np.rint((north-yy)/(north-south)*(image.height-1)).astype(int)
    inside=(px>=0)&(px<image.width)&(py>=0)&(py<image.height);out=np.zeros((*px.shape,3),dtype=np.uint8);out[:]=(255,0,255);out[inside]=source[py[inside],px[inside]]
    return Image.fromarray(out),dict(stations_m=stations.tolist(),cross_offsets_m=distances.tolist(),orientation='top=travel-left; bottom=travel-right',outside_samples=int((~inside).sum()),precision='visual interpretation aid, not survey measurement')
