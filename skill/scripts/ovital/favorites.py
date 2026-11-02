"""Bounded native CRUD, semantic snapshots, backups and independent save readback."""
import ctypes as C
import hashlib
import json
from pathlib import Path
import struct
import time
from uuid import uuid4
from . import protocol as P
from .validation import finite_number

class Favorites:
    def __init__(self,transport, *, allowed_ids=(),allowed_parents=(),backup_dir=None):
        self.transport=transport;self.allowed_ids={finite_number(value,'allowed object ID',positive=True,integer=True,maximum=0xffffffff) for value in allowed_ids};self.allowed_parents={finite_number(value,'allowed parent ID',positive=True,integer=True,maximum=0xffffffff) for value in allowed_parents};self.created=set();self.bound={}
        self.backup_dir=Path(backup_dir) if backup_dir else None

    def list(self,parent=1):
        _,raw=self.transport.request(20100,P.header(oid=parent,coordinate_flag=0))
        if not raw or len(raw)<16 or (len(raw)-16)%108:raise ValueError('unexpected native list size')
        nodes=[]
        for pos in range(16,len(raw),108):
            oid,typ=struct.unpack_from('<Ii',raw,pos)
            nodes.append(dict(id=oid,type=typ,name=raw[pos+8:pos+108].split(b'\0',1)[0].decode('utf-8'),parent_id=parent))
        return nodes

    def get_raw(self,oid):
        _,raw=self.transport.request(20101,P.header(oid=oid))
        if P.parse_header(raw)['id']!=oid:raise ValueError('response object identity mismatch')
        return raw
    def get(self,oid):return P.decode(self.get_raw(oid))
    def walk(self,root=1, *, max_objects=100000):
        max_objects=finite_number(max_objects,'traversal object budget',positive=True,integer=True)
        nodes=[];seen={root}
        def visit(parent,path):
            for node in self.list(parent):
                if node['id'] in seen:raise ValueError('cycle or duplicate ID in favorites tree')
                seen.add(node['id']);node['path']=path+[node['name']];nodes.append(node)
                if len(nodes)>max_objects:raise ValueError('favorites traversal bound exceeded')
                if node['type']==30:visit(node['id'],node['path'])
        visit(root,[]);return nodes
    def snapshot(self,root=1):
        return [dict(self.get(node['id']),path=node['path']) for node in self.walk(root)]
    def bind(self,oid):
        raw=self.get_raw(oid);self.bound[oid]=P.stable_bytes(raw)
        return P.decode(raw)
    def _existing(self,oid):
        if oid not in self.allowed_ids|self.created:raise PermissionError('object outside explicit mutation scope')
        raw=self.get_raw(oid)
        if oid not in self.created and (oid not in self.bound or P.stable_bytes(raw)!=self.bound[oid]):raise ValueError('existing object not bound or changed after binding')
        if self.backup_dir is None:raise ValueError('backup directory required before existing-object mutation')
        self.backup_dir.mkdir(parents=True,exist_ok=True)
        backup=self.backup_dir/(str(oid)+'-'+uuid4().hex+'.bin')
        with backup.open('xb') as stream:stream.write(raw)
        return raw,backup
    def _parent(self,parent):
        if parent not in self.allowed_parents|self.created:raise PermissionError('parent outside explicit creation/move scope')
        if parent!=1 and self.get(parent)['type']!=30:raise ValueError('destination is not a group')
    def create(self,parent,typ,body):
        self._parent(parent);P.decode(P.header(typ,parent=parent)+body)
        value,_=self.transport.request(20102,P.header(typ,parent=parent)+body,expect_response=False)
        oid=value&0xffffffff;self.created.add(oid)
        raw=self.get_raw(oid);actual=P.parse_header(raw)
        if (actual['parent_id'],actual['type'])!=(parent,typ):raise ValueError('created identity mismatch; reconcile before retry')
        # Header IDs differ, pointer slots can differ; payload semantic content cannot.
        expected=P.decode(P.header(typ,parent=parent,oid=oid)+body)
        if P.semantic_signature(P.decode(raw))!=P.semantic_signature(expected):raise ValueError('created content mismatch; preserve receipt and reconcile')
        self.bound[oid]=P.stable_bytes(raw);return self.get(oid)
    def create_group(self,parent,name,hidden=False):return self.create(parent,30,P.group_body(name,hidden=hidden))
    def create_geometry(self,parent,name,geometry,**options):
        typ,body=P.geometry_body(name,geometry,**options);return self.create(parent,typ,body)
    def update_metadata(self,oid, *, name=None,hidden=None):
        raw,backup=self._existing(oid);typ=P.parse_header(raw)['type'];cls=P.STRUCTURES.get(typ)
        if cls is None:raise ValueError('metadata editing unsupported for this object layout')
        updated=bytearray(raw);value=cls.from_buffer(updated,16)
        if name is not None:value.name=P.name_bytes(name)
        if hidden is not None:value.show=32 if hidden else 0
        self.transport.request(20102,bytes(updated),expect_response=False)
        actual=self.get_raw(oid)
        if P.stable_bytes(actual)!=P.stable_bytes(updated):raise ValueError('metadata update not read back exactly; do not replay')
        self.bound[oid]=P.stable_bytes(actual);return dict(object=P.decode(actual),backup=str(backup))
    def replace_geometry(self,oid,geometry, *, comment=None,style=None):
        raw,backup=self._existing(oid);before=P.decode(raw)
        typ,body=P.geometry_body(before['name'],geometry,comment=before.get('comment','') if comment is None else comment,style=style or before.get('native_style'))
        if typ!=before['type']:raise ValueError('type changes require explicit create-new workflow')
        # Keep all unedited native display/fill/metadata fields, not merely the
        # reduced GIS style dictionary. Only the explicit geometry/comment and
        # requested style fields are replaced.
        cls=P.STRUCTURES[typ];original=cls.from_buffer_copy(raw[16:]);generated=cls.from_buffer_copy(body)
        original.points=0;original.comment=0;original.count=generated.count;original.real=generated.real
        if typ==13:original.outer_count=generated.outer_count
        if style is not None:
            fields={'line':'line','width':'line_width','alpha':'line_alpha','line_type':'line_type','area':'area','area_alpha':'area_alpha'}
            for key in style:
                if typ==8 and key in {'line','width','alpha','line_type','closed'}:setattr(original.draw,'close' if key=='closed' else key,getattr(generated.draw,'close' if key=='closed' else key))
                elif typ==13 and key in fields:setattr(original,fields[key],getattr(generated,fields[key]))
        payload=raw[:16]+bytes(original)+body[C.sizeof(cls):];self.transport.request(20102,payload,expect_response=False)
        actual=self.get(oid);expected=P.decode(payload)
        if P.semantic_signature(actual)!=P.semantic_signature(expected):raise ValueError('replacement readback mismatch')
        self.bound[oid]=P.stable_bytes(self.get_raw(oid));return dict(object=actual,backup=str(backup))
    def move(self,ids,parent, *, index=-1):
        index=finite_number(index,'move index',minimum=-1,maximum=0x7fffffff,integer=True)
        ids=list(ids)
        if not ids or len(ids)!=len(set(ids)):raise ValueError('unique nonempty IDs required')
        self._parent(parent);before={oid:self._existing(oid) for oid in ids}
        for oid,(raw,_) in before.items():
            if oid==parent or (P.parse_header(raw)['type']==30 and parent in {n['id'] for n in self.walk(oid)}):raise ValueError('cannot move a group into itself or its descendant')
        payload=P.header(coordinate_flag=0)+struct.pack('<Ii',parent,index)+struct.pack('<'+'I'*len(ids),*ids)
        self.transport.request(20108,payload,expect_response=False)
        records=[]
        for oid,(raw,backup) in before.items():
            actual=self.get_raw(oid);wanted=bytearray(raw);struct.pack_into('<I',wanted,4,parent)
            # Position is receiver-managed; compare parent and complete semantic content.
            expected=P.decode(wanted);got=P.decode(actual)
            if P.semantic_signature(got)!=P.semantic_signature(expected):raise ValueError('move partial or mismatch; reconcile individual IDs')
            self.bound[oid]=P.stable_bytes(actual);records.append(dict(object=got,backup=str(backup)))
        return records
    def delete(self,ids):
        ids=list(ids)
        if not ids or len(set(ids))!=len(ids):raise ValueError('unique nonempty IDs required')
        before={oid:self._existing(oid) for oid in ids}
        for oid,(raw,_) in before.items():
            if P.parse_header(raw)['type']==30:
                descendants=self.walk(oid)
                if any(n['id'] not in self.allowed_ids|self.created for n in descendants):raise PermissionError('group contains objects outside deletion scope')
                for child in descendants:self._existing(child['id'])
        self.transport.request(20103,P.header(coordinate_flag=0)+struct.pack('<'+'I'*len(ids),*ids),expect_response=False)
        for oid,(raw,_) in before.items():
            parent=P.parse_header(raw)['parent_id']
            if parent not in ids and oid in {n['id'] for n in self.list(parent)}:raise ValueError('delete unverified; reconcile, do not blindly retry')
        return [dict(id=oid,backup=str(backup)) for oid,(_,backup) in before.items()]

def source_equal(before,after):
    if P.semantic_signature(before)!=P.semantic_signature(after):raise ValueError('source semantic content changed')
    return True

def resolve_save_action(transport, *, name='保存收藏夹对象',description='保存收藏夹对象到数据库中'):
    """Resolve the current accessible save action; never retain historical HWNDs."""
    import comtypes.client
    from comtypes import GUID
    IA=comtypes.client.GetModule('oleacc.dll').IAccessible
    U=transport.user;ole=C.OleDLL('oleacc');ole.AccessibleObjectFromWindow.argtypes=[C.c_void_p,C.c_ulong,C.POINTER(GUID),C.POINTER(C.POINTER(IA))]
    U.GetClassNameW.argtypes=[C.c_void_p,C.c_wchar_p,C.c_int];U.IsChild.argtypes=[C.c_void_p,C.c_void_p];U.IsChild.restype=C.c_bool
    CALLBACK=C.WINFUNCTYPE(C.c_bool,C.c_void_p,C.c_void_p);matches=[]
    @CALLBACK
    def visit(hwnd,_):
        cls=C.create_unicode_buffer(256);U.GetClassNameW(hwnd,cls,256)
        if 'toolbar' not in cls.value.lower():return True
        acc=C.POINTER(IA)();hr=ole.AccessibleObjectFromWindow(hwnd,0xfffffffc,C.byref(IA._iid_),C.byref(acc))
        if hr!=0:return True
        for child in range(1,acc.accChildCount+1):
            try:
                if acc.accName[child]==name and acc.accDescription[child]==description:matches.append((int(hwnd),child,acc))
            except (OSError,comtypes.COMError):pass
        return True
    U.EnumChildWindows.argtypes=[C.c_void_p,CALLBACK,C.c_void_p];U.EnumChildWindows(transport.omap,visit,None)
    if len(matches)!=1:raise ValueError('save action must resolve uniquely')
    hwnd,child,acc=matches[0]
    if not U.IsChild(transport.omap,hwnd):raise ValueError('save action no longer belongs to selected Ovital')
    return lambda:acc.accDoDefaultAction(child)

def save_and_verify(client,database,session_factory, *, object_ids,action=None,timeout=12.,poll=.2):
    """Save once; hash database and independently read semantic objects back.

    session_factory must create a NEW independent transport; no optimistic reuse.
    Database hash changes alone are not proof. No change may mean already saved;
    this is disclosed, not automatically retried. No source database copied here.
    """
    timeout=finite_number(timeout,'save timeout',minimum=0)
    poll=finite_number(poll,'save poll interval',positive=True)
    if not getattr(client.transport,'allow_write',False):raise PermissionError('save requires explicit write authority')
    path=Path(database).resolve()
    if not path.is_file():raise ValueError('explicit current favorites database required')
    expected={oid:client.get(oid) for oid in object_ids}
    if not expected:raise ValueError('save verification needs explicit object IDs')
    before=hashlib.sha256(path.read_bytes()).hexdigest();mtime=path.stat().st_mtime_ns
    (action or resolve_save_action(client.transport))()
    until=time.monotonic()+timeout;after=before
    while time.monotonic()<until:
        after=hashlib.sha256(path.read_bytes()).hexdigest()
        if after!=before:break
        time.sleep(poll)
    with session_factory() as independent:
        if independent is client.transport:raise ValueError('readback session is not independent')
        reader=Favorites(independent)
        for oid,record in expected.items():source_equal(record,reader.get(oid))
    return dict(saved_action_invoked=True,database_changed=after!=before,database_mtime_changed=path.stat().st_mtime_ns!=mtime,database_before_sha256=before,database_after_sha256=after,independent_semantic_readback=True,verified_ids=sorted(expected),persistence_evidence='database_changed_and_independent_session' if after!=before else 'unchanged_database_readback_only_not_fresh_persistence_proof')
