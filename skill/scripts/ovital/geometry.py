"""GIS geometry operations with explicit CRS/roles; no ArcGIS dependency."""
import math
from pyproj import CRS,Geod,Transformer
from shapely import normalize
from shapely.geometry import LineString,Point,Polygon,box,shape,mapping
from shapely.ops import transform,unary_union,split
from .validation import finite_number

def valid(geometry):
    def coordinates(value):
        if value.geom_type=='Polygon':
            yield from value.exterior.coords
            for ring in value.interiors:yield from ring.coords
        elif hasattr(value,'geoms'):
            for part in value.geoms:yield from coordinates(part)
        else:yield from value.coords
    for point in coordinates(geometry):
        for coordinate in point:finite_number(coordinate,'geometry coordinate')
    if geometry.is_empty or not geometry.is_valid:raise ValueError('nonempty valid source geometry required; repairs must be explicit')
    return geometry

def reproject(geometry,source_crs,target_crs):
    valid(geometry)
    source=CRS.from_user_input(source_crs);target=CRS.from_user_input(target_crs)
    if source.is_geographic:
        west,south,east,north=geometry.bounds
        if not (-180<=west<=east<=180 and -90<=south<=north<=90):raise ValueError('source geographic coordinates outside longitude/latitude range')
    return valid(transform(Transformer.from_crs(source,target,always_xy=True).transform,geometry))

def require_metric(crs):
    value=CRS.from_user_input(crs)
    if not value.is_projected or value.to_epsg()==3857 or any(axis.unit_conversion_factor!=1. for axis in value.axis_info[:2]):raise ValueError('suitable local metre projection required; not degrees, feet or Web Mercator')
    return value

def local_utm(geometry_wgs84):
    valid(geometry_wgs84);west,south,east,north=geometry_wgs84.bounds
    if not (-180<=west<=east<=180 and -80<=south<=north<=84) or east-west>5.5:raise ValueError('choose an explicit local metric CRS for broad/polar/dateline extent')
    lon,lat=geometry_wgs84.centroid.coords[0][:2];zone=min(60,int((lon+180)//6)+1)
    return CRS.from_epsg((32600 if lat>=0 else 32700)+zone)

def buffer_m(geometry,distance, *, crs,join_style='mitre',cap_style='flat'):
    require_metric(crs);valid(geometry)
    distance=finite_number(distance,'buffer distance')  # Negative polygon erosion remains legal.
    return valid(geometry.buffer(distance,join_style=join_style,cap_style=cap_style))

def overlay(geometries, *, clip=None,subtract=None):
    geometries=[valid(g) for g in geometries]
    if not geometries:raise ValueError('at least one source required')
    merged=unary_union(geometries);before=merged.area
    if clip is not None:merged=merged.intersection(valid(clip))
    clipped=merged.area
    if subtract is not None:merged=merged.difference(valid(subtract))
    if not merged.is_valid:raise ValueError('overlay produced invalid geometry')
    return merged,dict(union_area=before,clipped_area=clipped,removed_area=clipped-merged.area,result_area=merged.area)

def exact_deduplicate(features, *, role_key='role'):
    """Only exact geometry + explicit semantic role. Parallel opposite banks stay."""
    seen={};kept=[];duplicates=[]
    for feature in features:
        if role_key not in feature:raise ValueError('explicit semantic role required for deduplication')
        geometry=shape(feature['geometry'])
        key=(str(feature[role_key]),normalize(valid(geometry)).wkb)
        if key in seen:duplicates.append(dict(duplicate=feature.get('id'),kept=seen[key].get('id')))
        else:seen[key]=feature;kept.append(feature)
    return kept,duplicates

def replace_covered(old,new, *, coverage,crs):
    """Spatial local-version replacement; preserve every uncovered old piece."""
    require_metric(crs);valid(coverage)
    old_union=unary_union([valid(g) for g in old]);new_union=unary_union([valid(g) for g in new])
    outside=new_union.difference(coverage)
    if not outside.is_empty and (outside.area>1e-9 or outside.length>1e-7):raise ValueError('new version extends beyond explicitly declared replacement coverage')
    retained=old_union.difference(coverage);result=unary_union([retained,new_union])
    return result,dict(old_outside_coverage_preserved=retained.equals(result.difference(coverage)),old_measure=old_union.area if old_union.area else old_union.length,result_measure=result.area if result.area else result.length)

def close_road_pair(left,right, *, crs,max_cap_distance):
    """Close two explicitly paired boundaries, never a single arbitrary fragment."""
    require_metric(crs);valid(left);valid(right)
    max_cap_distance=finite_number(max_cap_distance,'maximum road cap distance',positive=True)
    if left.geom_type!='LineString' or right.geom_type!='LineString' or not left.is_simple or not right.is_simple:raise ValueError('two simple LineStrings required')
    if left.intersects(right):raise ValueError('paired boundaries cross or touch; inspect junction before closure')
    a=list(left.coords);b=list(right.coords)
    distance=lambda p,q:math.hypot(p[0]-q[0],p[1]-q[1])
    if distance(a[0],b[-1])+distance(a[-1],b[0])<distance(a[0],b[0])+distance(a[-1],b[-1]):b.reverse()
    gaps=[distance(a[0],b[0]),distance(a[-1],b[-1])]
    if max(gaps)>max_cap_distance:raise ValueError('end closure exceeds explicit allowed cap distance')
    result=Polygon(a+list(reversed(b)))
    valid(result)
    return result,dict(cap_lengths=gaps,source_boundaries_covered=result.boundary.covers(unary_union([left,right])))

def extend_endpoint(line,boundary, *, start=False,max_distance,crs):
    """Straight ray in existing end direction, bounded to nearest intersection."""
    require_metric(crs);valid(line);valid(boundary)
    max_distance=finite_number(max_distance,'maximum extension distance',positive=True)
    points=list(line.coords)
    if len(points)<2:raise ValueError('line and positive extension bound required')
    origin,inner=(points[0],points[1]) if start else (points[-1],points[-2])
    dx,dy=origin[0]-inner[0],origin[1]-inner[1];length=math.hypot(dx,dy)
    if length==0:raise ValueError('zero-length terminal segment')
    ray=LineString([origin,(origin[0]+dx/length*max_distance,origin[1]+dy/length*max_distance)])
    hit=ray.intersection(boundary.boundary if boundary.geom_type in ('Polygon','MultiPolygon') else boundary)
    def candidates(value):
        if value.geom_type=='Point':return [value]
        if hasattr(value,'geoms'):return [p for item in value.geoms for p in candidates(item)]
        return []
    options=[p for p in candidates(hit) if p.distance(Point(origin))>1e-8]
    if not options:raise ValueError('no unambiguous forward point intersection within extension bound')
    target=min(options,key=lambda p:p.distance(Point(origin)))
    if start:points.insert(0,target.coords[0])
    else:points.append(target.coords[0])
    return LineString(points)

def clip_side(polygon,divider,keep_point, *, crs):
    require_metric(crs);valid(polygon);valid(divider);valid(keep_point)
    pieces=list(split(polygon,divider).geoms)
    selected=[g for g in pieces if g.covers(keep_point)]
    if len(pieces)<2 or len(selected)!=1:raise ValueError('divider must split range and keep point must identify exactly one side')
    return selected[0]

def asymmetric_corridor(line,profile, *, crs,miter_limit=3.,sample_step=None):
    """Explicit station,left,right widths from physical baseline, all in metres.

    No author widths, hole filling or simplification defaults. Profile endpoint
    station must equal actual length. Invalid/self-contact envelopes are refused.
    """
    import numpy as np
    require_metric(crs);valid(line)
    miter_limit=finite_number(miter_limit,'miter limit',minimum=1.)
    if sample_step is not None:sample_step=finite_number(sample_step,'corridor sample step',positive=True)
    if line.geom_type!='LineString' or not line.is_simple or line.length<=0:raise ValueError('simple positive-length baseline required')
    values=np.asarray(profile,dtype=float);length=finite_number(line.length,'baseline length',positive=True)
    if values.ndim!=2 or values.shape[1]!=3 or len(values)<2 or not np.isfinite(values).all():raise ValueError('profile rows must be station,left,right')
    if values[0,0]!=0 or abs(values[-1,0]-length)>1e-7 or not (np.diff(values[:,0])>0).all() or (values[:,1:]<0).any():raise ValueError('profile must span entire baseline monotonically with nonnegative widths')
    original=np.asarray(line.coords);lens=np.hypot(*np.diff(original,axis=0).T)
    if sample_step is not None and finite_number(length/sample_step,'corridor sample count',positive=True)>1000000:raise ValueError('corridor sampling exceeds one-million-station safety bound')
    extra=np.arange(0,length,sample_step) if sample_step is not None else []
    stations=np.unique(np.r_[0,np.cumsum(lens),values[:,0],extra]);q=np.array([line.interpolate(float(s)).coords[0] for s in stations]);vec=np.diff(q,axis=0);sizes=np.hypot(*vec.T)
    if sizes.min()<1e-9:raise ValueError('duplicate/zero-length baseline segment')
    normal=np.column_stack((-vec[:,1]/sizes,vec[:,0]/sizes))
    def edge(width,sign):
        starts=q[:-1]+normal*sign*width[:-1,None];ends=q[1:]+normal*sign*width[1:,None];result=[starts[0]]
        for i in range(1,len(q)-1):
            a,b=starts[i-1],ends[i-1];c,d=starts[i],ends[i];u,v=b-a,d-c;cross=u[0]*v[1]-u[1]*v[0]
            if abs(cross)<1e-10:point=(b+c)/2
            else:
                z=c-a;point=a+(z[0]*v[1]-z[1]*v[0])/cross*u
            if np.linalg.norm(point-q[i])>miter_limit*max(width[i],1e-9):result.extend([b,c])
            else:result.append(point)
        result.append(ends[-1]);return result
    left=edge(np.interp(stations,values[:,0],values[:,1]),1);right=edge(np.interp(stations,values[:,0],values[:,2]),-1)
    return valid(Polygon(left+list(reversed(right))))

def measure(geometry, *, crs):
    """Explicit projected and geodesic figures; no boundary-length => centerline claim."""
    source=CRS.from_user_input(crs);valid(geometry);geographic=reproject(geometry,source,4326);geod=Geod(ellps='WGS84')
    from shapely.geometry.polygon import orient
    def oriented(g):
        if g.geom_type=='Polygon':return orient(g,sign=1)
        if hasattr(g,'geoms'):
            from shapely.geometry import GeometryCollection
            return GeometryCollection([oriented(x) for x in g.geoms])
        return g
    area,perimeter=geod.geometry_area_perimeter(oriented(geographic))
    result=dict(geodesic_area_m2=abs(area),geodesic_perimeter_m=perimeter,geodesic_length_m=geod.geometry_length(geographic),source_crs=source.to_string())
    if source.is_projected:
        require_metric(source);result.update(projected_area_m2=geometry.area,projected_length_m=geometry.length)
    return result
