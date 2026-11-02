"""Portable CLI. Offline file/geometry default; native contact needs --allow-live.

Examples: python ovital_tool.py kml-info input.kmz
          python ovital_tool.py calculate plan.json --output result.json
          python ovital_tool.py native plan.json --receipt receipt.json --allow-live
Write-native additionally needs --allow-write and explicit per-ID/parent scope.
Flags record caller intent; they do not replace user authorization.
"""
import argparse
import json
from pathlib import Path
import sys

def _reject_json_constant(value):raise ValueError('non-standard JSON numeric constant rejected: '+value)
def _finite_json_float(value):
    from ovital.validation import finite_number
    return finite_number(float(value),'JSON floating-point number')
def load(path):return json.loads(Path(path).read_text('utf-8-sig'),parse_constant=_reject_json_constant,parse_float=_finite_json_float)
def save(path,value):
    path=Path(path)
    if path.exists():raise FileExistsError('refusing to overwrite existing output/receipt')
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False)

def calculate(plan):
    from shapely.geometry import shape,mapping
    from ovital import geometry as G
    operation=plan['operation'];crs=plan.get('crs');geometries=[shape(g) for g in plan.get('geometries',[])];notes={}
    if operation=='buffer':result=G.buffer_m(geometries[0],plan['distance_m'],crs=crs)
    elif operation=='overlay':result,notes=G.overlay(geometries,clip=shape(plan['clip']) if plan.get('clip') else None,subtract=shape(plan['subtract']) if plan.get('subtract') else None)
    elif operation=='road-pair':result,notes=G.close_road_pair(*geometries,crs=crs,max_cap_distance=plan['max_cap_distance_m'])
    elif operation=='corridor':result=G.asymmetric_corridor(geometries[0],plan['profile'],crs=crs)
    elif operation=='extend':result=G.extend_endpoint(*geometries,crs=crs,start=plan.get('start',False),max_distance=plan['max_distance_m'])
    elif operation=='reproject':result=G.reproject(geometries[0],crs,plan['target_crs']);crs=plan['target_crs']
    elif operation=='replace-covered':result,notes=G.replace_covered([shape(g) for g in plan['old']],[shape(g) for g in plan['new']],coverage=shape(plan['coverage']),crs=crs)
    else:raise ValueError('unknown offline operation')
    return dict(crs=crs,geometry=mapping(result),checks=notes,measurement=G.measure(result,crs=crs) if not result.is_empty else None,scope='software geometry only; physical baseline and imagery accuracy not certified')

def native(plan,args):
    if not args.allow_live:raise PermissionError('native contact requires explicit --allow-live; no app launched')
    from ovital.session import WinMessageTransport
    from ovital.favorites import Favorites,save_and_verify
    from ovital.imagery import map_info,screen_bounds,export_cached_map
    action=plan['action'];writing=action in ('mutate','save')
    if writing and not args.allow_write:raise PermissionError('native mutation/save requires --allow-write and authorized scope')
    if Path(args.receipt).exists():raise FileExistsError('existing receipt must be reconciled before a fresh run')
    result=dict(state='prepared',action=action,results=[],not_verified=['visual application appearance','survey accuracy'])
    path=Path(args.receipt);save(path,result)
    def checkpoint():
        temp=path.with_suffix(path.suffix+'.tmp')
        with temp.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2,allow_nan=False)
        temp.replace(path)
    kwargs={key:plan[key] for key in ('hwnd','expected_pid') if key in plan}
    with WinMessageTransport(allow_write=writing,**kwargs) as transport:
        result.update(process_id=transport.pid,foreground_before=transport.foreground_before)
        api=Favorites(transport,allowed_ids=plan.get('allowed_ids',[]),allowed_parents=plan.get('allowed_parents',[]),backup_dir=plan.get('backup_dir'))
        try:
            if action=='snapshot':result['objects']=api.snapshot(plan.get('root',1))
            elif action=='map-info':result['map_info']=map_info(transport);result['screen_bounds']=screen_bounds(transport)
            elif action=='export-cache':result['map_export']=export_cached_map(transport,plan['output'],plan['bounds'],level=plan.get('level'),map_type=plan.get('map_type'))
            elif action=='mutate':
                for oid in plan.get('allowed_ids',[]):api.bind(oid)
                for number,operation in enumerate(plan['operations']):
                    result.update(state='operation_requested_effect_unknown',operation_index=number,requested_operation=operation);checkpoint()
                    kind=operation['operation']
                    if kind=='create-group':value=api.create_group(operation['parent'],operation['name'],hidden=operation.get('hidden',False))
                    elif kind=='create-geometry':value=api.create_geometry(operation['parent'],operation['name'],operation['geometry'],comment=operation.get('comment',''),style=operation.get('style'))
                    elif kind=='metadata':value=api.update_metadata(operation['id'],name=operation.get('name'),hidden=operation.get('hidden'))
                    elif kind=='replace-geometry':value=api.replace_geometry(operation['id'],operation['geometry'],comment=operation.get('comment'),style=operation.get('style'))
                    elif kind=='move':value=api.move(operation['ids'],operation['parent'],index=operation.get('index',-1))
                    elif kind=='delete':value=api.delete(operation['ids'])
                    else:raise ValueError('unknown native mutation')
                    result['results'].append(dict(operation=kind,value=value));result['state']='operation_readback_verified';checkpoint()
            elif action!='save':raise ValueError('unknown native action')
            if action=='save' or (writing and plan.get('save') is True):
                result['state']='save_requested_effect_unknown';checkpoint()
                result['save']=save_and_verify(api,plan['database'],lambda:WinMessageTransport(hwnd=transport.omap,expected_pid=transport.pid),object_ids=plan['verify_ids'])
            result['state']='completed_with_declared_evidence'
        except BaseException as exc:
            result.update(state='failed_or_unknown_do_not_replay',error=type(exc).__name__+': '+str(exc));checkpoint();raise
        finally:
            result['api_log']=transport.log;checkpoint()
    result['foreground_after']=transport.foreground_after;result['foreground_unchanged']=transport.foreground_before==transport.foreground_after;checkpoint()
    return result

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest='command',required=True)
    info=commands.add_parser('kml-info');info.add_argument('input')
    convert=commands.add_parser('kml-export');convert.add_argument('input');convert.add_argument('--output',required=True)
    calc=commands.add_parser('calculate');calc.add_argument('input');calc.add_argument('--output',required=True)
    live=commands.add_parser('native');live.add_argument('input');live.add_argument('--receipt',required=True);live.add_argument('--allow-live',action='store_true');live.add_argument('--allow-write',action='store_true')
    args=parser.parse_args(argv)
    if args.command.startswith('kml'):
        from ovital import kml
        if args.command=='kml-info':
            rows=kml.read(args.input);result=dict(features=len(rows),geometry_types=[row['geometry']['type'] for row in rows],paths=[row['path'] for row in rows])
        else:result=kml.write(args.output,load(args.input))
    elif args.command=='calculate':result=calculate(load(args.input));save(args.output,result)
    else:result=native(load(args.input),args)
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
