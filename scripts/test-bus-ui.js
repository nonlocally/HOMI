#!/usr/bin/env node
// Run the embedded dashboard's actual JavaScript without a browser dependency.
// This checks auth/race handling, untrusted roster rendering, and invite scope;
// visual layout is intentionally left to real-browser review.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {TextEncoder} = require('node:util');
const html = fs.readFileSync(path.join(__dirname, '../lib/bus_ui.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

function element(tag) {
  return {
    tagName:tag, children:[], attrs:{}, dataset:{}, className:'', value:'', hidden:false,
    disabled:false, open:false, checked:false, required:false, _text:'', events:{},scrollHeight:0,scrollTop:0,clientHeight:0,
    set textContent(value) {this.children=[];this._text=String(value);},
    get textContent() {return this._text+this.children.map(child=>child.textContent).join('');},
    set innerHTML(_) {throw new Error('Untrusted HTML must never be parsed');},
    setAttribute(name,value) {this.attrs[name]=String(value);},
    append(...children) {this.children.push(...children);},
    replaceChildren(...children) {this._text='';this.children=children;},
    addEventListener(name,handler) {(this.events[name] ||= []).push(handler);},
    async fire(name,values={}) {await Promise.all((this.events[name] || []).map(handler => handler({target:this,...values,preventDefault(){}})));},
    showModal() {this.open=true;},
    close() {this.open=false;this.fire('close');},
  };
}
const flush = () => new Promise(resolve => setImmediate(() => setImmediate(resolve)));
function page({hash='#token=secret', storedToken='', snapshot=fixture(), origin='http://127.0.0.1:7331', bootstrap=null, graphRenderer=null, pathname='/', search=''}={}) {
  const ids={},nodes=[],requests=[],copies=[],storage=new Map(),history=[],timers=[],navigations=[];
  let handler = request => request.url === '/_bus/session' ? (bootstrap || {ok:false,status:404}) : ({ok:true,...(request.op === 'snapshot' ? snapshot : {})});
  if (storedToken) storage.set('communicate.bus.dashboard.token',storedToken);
  for (const match of html.matchAll(/<([a-z][a-z0-9-]*)\b([^<>]*)>/g)) {
    const node=element(match[1]);
    for (const attr of match[2].matchAll(/([a-z][a-z0-9-]*)(?:="([^"]*)")?/g)) {
      const name=attr[1],value=attr[2] || '';
      node.attrs[name]=value;
      if (name==='id') {node.id=value;ids[value]=node;}
      if (name==='class') node.className=value;
      if (name==='value') node.value=value;
      if (name==='hidden') node.hidden=true;
      if (name.startsWith('data-')) node.dataset[name.slice(5)]=value;
    }
    nodes.push(node);
  }
  ids['invite-ttl'].value='3600';
  const document={hidden:false,getElementById:id=>ids[id],createElement:element,addEventListener(){},querySelectorAll(selector){
    if (selector==='dialog') return nodes.filter(node=>node.tagName==='dialog');
    if (selector.startsWith('.')) return nodes.filter(node=>node.className.split(' ').includes(selector.slice(1)));
    return nodes.filter(node=>Object.hasOwn(node.attrs,selector.slice(1,-1)));
  }};
  const context={
    CommunicateGraph:graphRenderer,
    document,URL,URLSearchParams,TextEncoder,AbortController,Map,Set,Date,crypto:require('node:crypto').webcrypto,
    location:{hash,pathname,search,origin,assign:url=>navigations.push(url)},
    history:{replaceState(_state,_title,url){history.push(url);context.location.hash='';}},
    sessionStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},
    navigator:{clipboard:{async writeText(text){copies.push(text);}}},
    confirm:()=>true,
    setTimeout:()=>1,clearTimeout(){},setInterval:fn=>timers.push(fn),
    btoa:value=>Buffer.from(value,'binary').toString('base64'),
    async fetch(url,options) {
      const request={url,options,...JSON.parse(options.body || '{}')};requests.push(request);
      const result=await handler(request);
      return {ok:result.status===undefined || result.status<400,status:result.status || 200,json:async()=>result};
    },
  };
  vm.runInNewContext(script,context,{filename:'bus_ui.html'});
  return {ids,nodes,requests,copies,storage,history,timers,navigations,context,setHandler:fn=>{handler=fn;}};
}
function fixture() {
  const now=Date.now()/1000;
  const claude={id:'a',name:'research',kind:'claude',user:'aadarwal',device:'lab-mac',device_id:'device-a',device_metadata:{hostname:'lab-mac.local',platform:'darwin',tailscale_hostname:'aadarwal-mini',tailscale_dns_name:'aadarwal-mini.example.ts.net'},status:'live',last_seen:now,description:'Designing the next experiment',buses:['general','photonics']};
  const codex={id:'b',name:'builder',kind:'codex',user:'peer',device:'workstation',device_id:'device-b',device_metadata:{hostname:'linux-workstation',platform:'linux'},status:'queueable',last_seen:now-30,description:'Reviewing the simulation',buses:['general']};
  const unsafe={id:'c',name:'<img src=x onerror=alert(1)>',kind:'claude',user:null,device:'guest-laptop',device_id:'p1',status:'offline',last_seen:now-400,description:'<script>bad()</script>',buses:['photonics']};
  return {ok:true,server_id:'fixture',is_admin:true,buses:[{name:'general',visibility:'open',agents:[claude,codex]},{name:'photonics',visibility:'private',agents:[claude,unsafe]}],principals:[{id:'p1',device_id:'p1',user:null,device:'guest-laptop',buses:['photonics'],revoked:false}]};
}

function accountFixture(user='owner') {
  const capabilities=(invite=false,manage_members=false,leave=false)=>({invite,manage_members,leave});
  const owner=user==='owner';
  return {ok:true,server_id:'account-fixture',browser_session:true,read_only:true,is_admin:false,
    can_create_bus:true,user,users:[{id:'owner'},{id:'colleague'},{id:'new-person'}],
    chat:{enabled:false,buses:[]},buses:[
      {name:'general',visibility:'open',owner_user:null,role:'viewer',capabilities:capabilities(),agents:[]},
      {name:'study',visibility:'private',owner_user:'owner',role:owner?'owner':'member',
        capabilities:capabilities(true,owner,!owner),agents:[],
        ...(owner ? {members:[{user:'owner',role:'owner'},{user:'colleague',role:'member'}]} : {})},
      {name:'other-study',visibility:'private',owner_user:'someone-else',role:'member',
        capabilities:capabilities(true,false,true),agents:[]}]};
}

(async () => {
  const p=page();await flush();
  assert.deepEqual(p.history,['/'],'consume fragment before sending any requests');
  assert.equal(p.context.location.hash,'');
  assert.equal(p.requests[0].url,'/v1');
  assert.equal(p.requests[0].options.headers.Authorization,'Bearer secret');
  assert.equal(p.requests[0].options.credentials,'same-origin');
  assert.ok(!JSON.stringify(p.requests[0].op).includes('secret'));
  assert.equal(p.storage.get('communicate.bus.dashboard.token'),'secret');
  assert.equal(p.ids.app.hidden,false);
  assert.equal(p.ids['agent-rows'].children.length,3,'aggregate roster deduplicates shared agents');
  assert.equal(p.ids['live-count'].textContent,'1');
  assert.equal(p.ids['queueable-count'].textContent,'1');
  assert.equal(p.ids['device-count'].textContent,'3');
  assert.ok(p.ids['agent-rows'].textContent.includes('<img src=x onerror=alert(1)>'),'untrusted names remain literal text');
  console.log('ok token fragment consumption, bearer auth, roster deduplication, safe text rendering');

  await p.ids['bus-nav'].children[2].fire('click');
  assert.equal(p.ids['page-title'].textContent,'photonics');
  assert.equal(p.ids['agent-rows'].children.length,2);
  assert.equal(p.ids['register-command'].textContent,'communicate bus register --bus photonics');
  p.ids.search.value='guest';await p.ids.search.fire('input');
  assert.equal(p.ids['agent-rows'].children.length,1);
  assert.ok(p.ids['agent-rows'].textContent.includes('guest-laptop'));
  const live=p.nodes.find(node=>node.dataset.status==='live');await live.fire('click');
  assert.equal(p.ids.empty.hidden,false);assert.equal(p.ids['empty-register'].hidden,true);
  assert.equal(p.ids['empty-title'].textContent,'No agents match this view.');
  console.log('ok bus selection, registration command, search and combined status filtering');

  p.setHandler(()=>({ok:false,status:503,error:'Unavailable'}));await p.ids.refresh.fire('click');
  assert.equal(p.ids['stale-notice'].hidden,false);
  assert.ok(p.ids['stale-notice'].textContent.includes('may be stale'));
  assert.equal(p.ids.app.hidden,false,'keep last good roster visible during network errors');
  p.setHandler(()=>({ok:false,status:401,error:'Invalid token'}));await p.ids.refresh.fire('click');
  assert.equal(p.ids.app.hidden,true);assert.equal(p.ids.login.hidden,false);
  assert.equal(p.storage.size,0);
  console.log('ok stale roster indication and revoked-token logout');

  const member=page({snapshot:{...fixture(),is_admin:false,principals:undefined}});await flush();
  assert.equal(member.ids['invite-button'].hidden,true);
  assert.equal(member.ids['sidebar-create'].hidden,true);
  assert.equal(member.ids['devices-button'].hidden,true);
  assert.equal(member.ids['actions-heading'].hidden,true);
  console.log('ok member view hides administrative actions');

  const ownerData=accountFixture(), owner=page({snapshot:ownerData,origin:'https://bus.example'});await flush();
  assert.equal(owner.ids['create-button'].hidden,false);
  assert.equal(owner.ids['devices-button'].hidden,true,'account owner cannot revoke broker devices');
  assert.equal(owner.ids['inbox-button'].hidden,true,'private bus ownership does not grant human chat');
  owner.setHandler(request=>{
    const bus=ownerData.buses.find(b=>b.name===request.bus);
    if (request.op==='create') ownerData.buses.push({name:request.bus,owner_user:'owner',role:'owner',agents:[],
      capabilities:{invite:true,manage_members:true,leave:false},members:[{user:'owner',role:'owner'}]});
    if (request.op==='member_add') bus.members.push({user:request.user,role:'member'});
    if (request.op==='member_remove') bus.members=bus.members.filter(m=>m.user!==request.user);
    return request.op==='snapshot' ? ownerData : {ok:true};
  });
  await owner.ids['create-button'].fire('click');owner.ids['new-bus'].value='new-study';
  await owner.ids['create-form'].fire('submit');
  assert.equal(owner.ids['page-title'].textContent,'new-study');
  assert.equal(owner.ids['members-button'].hidden,false);
  assert.equal(owner.ids['leave-button'].hidden,true,'owner cannot leave own bus');
  await owner.ids['bus-nav'].children[2].fire('click');await owner.ids['members-button'].fire('click');
  assert.equal(owner.ids['member-list'].children[0].children[1].textContent,'Owner');
  assert.deepEqual(owner.ids['member-user'].children.map(el=>el.value),['','new-person']);
  owner.ids['member-user'].value='new-person';await owner.ids['member-form'].fire('submit');
  assert.ok(owner.requests.some(r=>r.op==='member_add' && r.bus==='study' && r.user==='new-person'));
  const removePerson=children(owner.ids['member-list']).find(el=>el.attrs['aria-label']==='Remove new-person from study');
  await removePerson.fire('click');
  assert.ok(owner.requests.some(r=>r.op==='member_remove' && r.bus==='study' && r.user==='new-person'));
  assert.equal(ownerData.buses[1].members.length,2);
  assert.equal(owner.ids['actions-heading'].hidden,true,'owner receives no global agent removal action');
  console.log('ok admitted browser creates private bus and manages only its members without admin or chat authority');

  owner.ids['members-dialog'].close();await owner.ids['invite-button'].fire('click');
  assert.deepEqual(owner.ids['invite-bus'].children.map(el=>el.value),['study','other-study','new-study']);
  assert.deepEqual(owner.ids['invite-user'].children.map(el=>el.value),['','owner','colleague']);
  owner.ids['invite-user'].value='new-person';let accountRequestCount=owner.requests.length;
  await owner.ids['invite-form'].fire('submit');
  assert.equal(owner.requests.length,accountRequestCount,'admitted non-member cannot receive owned-bus invitation');
  owner.ids['invite-user'].value='colleague';
  owner.setHandler(r=>r.op==='invite' ? {ok:true,invite:'fixture-owner-invite',expires_at:Date.now()/1000+60} : ownerData);
  await owner.ids['invite-form'].fire('submit');
  assert.equal(owner.requests.at(-1).user,'colleague');assert.equal(owner.requests.at(-1).bus,'study');
  ownerData.buses[1].members=ownerData.buses[1].members.filter(m=>m.user!=='colleague');
  await owner.ids.refresh.fire('click');
  assert.equal(owner.ids['invite-output'].textContent,'','removed member clears displayed invitation');
  assert.equal(owner.ids['invite-user'].value,'owner');
  owner.ids['invite-bus'].value='other-study';await owner.ids['invite-bus'].fire('change');
  assert.deepEqual(owner.ids['invite-user'].children.map(el=>el.value),['','owner'],'member of another owner bus can invite only own device');
  console.log('ok invitation bus and recipient choices follow per-bus membership, including removal while open');

  const colleagueData=accountFixture('colleague'), colleague=page({snapshot:colleagueData,origin:'https://bus.example'});await flush();
  await colleague.ids['bus-nav'].children[2].fire('click');
  assert.equal(colleague.ids['members-button'].hidden,true);assert.equal(colleague.ids['leave-button'].hidden,false);
  await colleague.ids['invite-button'].fire('click');
  assert.equal(colleague.ids['invite-user'].value,'colleague');assert.equal(colleague.ids['invite-user'].disabled,true);
  colleague.ids['invite-user'].value='owner';accountRequestCount=colleague.requests.length;
  await colleague.ids['invite-form'].fire('submit');assert.equal(colleague.requests.length,accountRequestCount);
  colleague.ids['invite-dialog'].close();
  colleague.setHandler(r=>{if(r.op==='member_remove') colleagueData.buses=colleagueData.buses.filter(b=>b.name!==r.bus);return r.op==='snapshot'?colleagueData:{ok:true};});
  await colleague.ids['leave-button'].fire('click');
  assert.ok(colleague.requests.some(r=>r.op==='member_remove' && r.bus==='study' && r.user==='colleague'));
  assert.equal(colleague.ids['page-title'].textContent,'All buses');
  assert.ok(colleagueData.buses.some(b=>b.name==='other-study'),'leaving one bus preserves the other');
  console.log('ok ordinary member can enroll only own device and leave only own scoped membership');

  const revokedData=accountFixture(), revoked=page({snapshot:revokedData,origin:'https://bus.example'});await flush();
  await revoked.ids['bus-nav'].children[2].fire('click');await revoked.ids['members-button'].fire('click');
  const oldRemove=children(revoked.ids['member-list']).find(el=>el.tagName==='button');
  revokedData.can_create_bus=false;revokedData.buses[1].capabilities={invite:false,manage_members:false,leave:false};
  revoked.setHandler(()=>revokedData);await revoked.ids.refresh.fire('click');accountRequestCount=revoked.requests.length;
  assert.equal(revoked.ids['member-list'].children.length,0);assert.equal(revoked.ids['member-submit'].disabled,true);
  await oldRemove.fire('click');revoked.ids['member-user'].value='new-person';await revoked.ids['member-form'].fire('submit');
  await revoked.ids['create-button'].fire('click');
  assert.equal(revoked.requests.length,accountRequestCount,'old member controls cannot dispatch after capability revocation');
  assert.equal(revoked.ids['create-dialog'].open,false);assert.equal(revoked.ids['members-button'].hidden,true);
  const labelsOnly=accountFixture();delete labelsOnly.can_create_bus;
  for(const bus of labelsOnly.buses) delete bus.capabilities;
  const labelPage=page({snapshot:labelsOnly});await flush();await labelPage.ids['bus-nav'].children[2].fire('click');
  assert.equal(labelPage.ids['create-button'].hidden,true);assert.equal(labelPage.ids['invite-button'].hidden,true);
  assert.equal(labelPage.ids['members-button'].hidden,true,'role labels and matching owner names do not imply authority');
  console.log('ok permission revocation invalidates open member controls; role labels alone grant nothing');

  const changedInvite=page({snapshot:accountFixture(),origin:'https://bus.example'});await flush();
  await changedInvite.ids['invite-button'].fire('click');let resolveChanged;
  changedInvite.setHandler(()=>new Promise(resolve=>{resolveChanged=resolve;}));
  const changedPending=changedInvite.ids['invite-form'].fire('submit');await flush();
  changedInvite.ids['invite-bus'].value='other-study';await changedInvite.ids['invite-bus'].fire('change');
  resolveChanged({ok:true,invite:'obsolete-scope-secret',expires_at:Date.now()/1000+60});await changedPending;
  assert.equal(changedInvite.ids['invite-output'].textContent,'');assert.equal(changedInvite.ids['invite-result'].hidden,true);
  assert.equal(changedInvite.ids['invite-submit'].disabled,false);
  console.log('ok changing invitation scope rejects an in-flight credential for the previous bus');

  const denied=page({snapshot:accountFixture()});await flush();
  denied.setHandler(()=>({ok:false,status:403,error:'Membership changes are not permitted'}));
  await denied.ids['create-button'].fire('click');denied.ids['new-bus'].value='denied';await denied.ids['create-form'].fire('submit');
  assert.equal(denied.ids['create-dialog'].open,true);assert.equal(denied.ids['page-title'].textContent,'All buses');
  assert.ok(denied.ids['create-error'].textContent.includes('not permitted'));
  denied.ids['create-dialog'].close();await denied.ids['bus-nav'].children[2].fire('click');await denied.ids['members-button'].fire('click');
  denied.ids['member-user'].value='new-person';await denied.ids['member-form'].fire('submit');
  assert.ok(denied.ids['member-error'].textContent.includes('not permitted'));
  assert.equal(denied.ids['member-list'].children.length,2,'failed write does not add a phantom member');
  console.log('ok broker-denied creation and member changes remain visible failures without optimistic state');

  const labelledData=accountFixture();
  labelledData.users.forEach(user=>{user.label='<label-' + user.id + '>';});
  const labelled=page({snapshot:labelledData,origin:'https://bus.example'});await flush();
  await labelled.ids['bus-nav'].children[2].fire('click');await labelled.ids['members-button'].fire('click');
  assert.equal(labelled.ids['member-user'].children[1].textContent,'<label-new-person>');
  assert.equal(labelled.ids['member-user'].children[1].value,'new-person');
  assert.ok(labelled.ids['page-description'].textContent.includes('<label-owner>'));
  labelled.ids['member-user'].value='new-person';await labelled.ids['member-form'].fire('submit');
  assert.ok(labelled.requests.some(r=>r.op==='member_add' && r.user==='new-person'));
  labelled.ids['members-dialog'].close();await labelled.ids['invite-button'].fire('click');
  assert.equal(labelled.ids['invite-user'].children[2].textContent,'<label-colleague>');
  assert.equal(labelled.ids['invite-user'].children[2].value,'colleague');
  console.log('ok account display labels are literal text while membership and invitation values remain canonical IDs');

  const readerData={...fixture(),is_admin:false,read_only:true,browser_session:true,user:'owui.00000000-0000-4000-8000-000000000001',display_name:'<img src=x onerror=alert(1)>',principals:undefined};
  const reader=page({snapshot:readerData});await flush();
  assert.equal(reader.ids['current-user'].textContent,readerData.display_name,'display labels render as literal text');
  assert.equal(reader.ids['current-user'].hidden,false);
  assert.equal(reader.ids.role.textContent,'Directory reader');
  assert.equal(reader.ids['invite-button'].hidden,true,'a display label never grants administration');
  assert.ok(!reader.ids['user-filter'].textContent.includes(readerData.display_name),'display labels do not replace account identities');
  reader.setHandler(()=>({...readerData,display_name:undefined}));await reader.ids.refresh.fire('click');
  assert.equal(reader.ids['current-user'].textContent,readerData.user,'older browser snapshots use the stable account label');
  reader.setHandler(()=>fixture());await reader.ids.refresh.fire('click');
  assert.equal(reader.ids['current-user'].hidden,true,'device-token views do not retain a previous browser identity');
  console.log('ok browser display name is text-only with stable identity fallback and unchanged roles');

  const i=page();await flush();await i.ids['invite-button'].fire('click');
  assert.equal(i.ids['local-only-label'].hidden,false);
  await i.ids['invite-form'].fire('submit');
  assert.ok(i.ids['invite-error'].textContent.includes('only works on this device'));
  assert.equal(i.requests.length,1,'no invitation generated before local-only acknowledgement');
  i.ids['invite-url'].value='http://someone.example';await i.ids['invite-url'].fire('input');
  await i.ids['invite-form'].fire('submit');
  assert.ok(i.ids['invite-error'].textContent.includes('HTTPS'));
  assert.equal(i.requests.length,1,'remote HTTP invitations are refused');
  i.ids['invite-url'].value='https://broker.example';await i.ids['invite-url'].fire('input');
  i.ids['invite-bus'].value='photonics';
  i.setHandler(request=>request.op==='invite' ? {ok:true,invite:'single-use-✓',expires_at:Date.now()/1000+3600} : fixture());
  await i.ids['invite-form'].fire('submit');
  assert.equal(i.requests.at(-1).op,'invite');assert.equal(i.requests.at(-1).bus,'photonics');assert.equal(i.requests.at(-1).ttl,3600);
  const command=i.ids['invite-output'].textContent;
  assert.ok(command.startsWith('communicate bus connect commbus1.'));
  const encoded=command.split('commbus1.')[1];
  assert.deepEqual(JSON.parse(Buffer.from(encoded,'base64url').toString()),{url:'https://broker.example',invite:'single-use-✓'});
  await i.ids['copy-invite'].fire('click');assert.equal(i.copies[0],command);
  await i.ids['revoke-invite'].fire('click');
  assert.equal(i.requests.at(-1).op,'invite_revoke');assert.equal(i.requests.at(-1).invite,'single-use-✓');
  assert.equal(i.ids['invite-output'].textContent,'Invitation revoked.');
  assert.equal(i.ids['copy-invite'].disabled,true);assert.equal(i.ids['revoke-invite'].hidden,true);
  i.ids['invite-dialog'].close();assert.equal(i.ids['invite-output'].textContent,'');
  console.log('ok invitation scope, HTTPS enforcement, encoding, copy, revocation, and secret cleanup');

  const admin=page();await flush();
  let current=fixture();
  admin.setHandler(request=>{
    if (request.op==='create') current.buses.push({name:request.bus,visibility:'private',agents:[]});
    if (request.op==='leave') for (const bus of current.buses) {
      if (bus.name===request.bus) bus.agents=bus.agents.filter(agent=>agent.id!==request.agent);
      for (const agent of bus.agents) if (agent.id===request.agent) agent.buses=agent.buses.filter(name=>name!==request.bus);
    }
    if (request.op==='revoke') current.principals=current.principals.filter(principal=>principal.id!==request.principal);
    return request.op==='snapshot' ? current : {ok:true};
  });
  await admin.ids['create-button'].fire('click');admin.ids['new-bus'].value='optics';
  await admin.ids['create-form'].fire('submit');
  assert.ok(admin.requests.some(request=>request.op==='create' && request.bus==='optics'));
  assert.equal(admin.ids['page-title'].textContent,'optics');
  assert.equal(admin.ids['create-dialog'].open,false);
  await admin.ids['bus-nav'].children[2].fire('click');
  const remove=admin.ids['agent-rows'].children[0].children.at(-1).children[0];
  await remove.fire('click');
  assert.ok(admin.requests.some(request=>request.op==='leave' && request.agent==='a' && request.bus==='photonics'));
  assert.equal(admin.ids['agent-rows'].children.length,1);
  await admin.ids['devices-button'].fire('click');
  await admin.ids['device-list'].children[0].children[1].fire('click');
  assert.ok(admin.requests.some(request=>request.op==='revoke' && request.principal==='p1'));
  assert.ok(admin.ids['device-list'].textContent.includes('No invited devices'));
  console.log('ok create, remove membership, and revoke-device operations update their views');

  const closedInvite=page();await flush();await closedInvite.ids['invite-button'].fire('click');
  closedInvite.ids['invite-url'].value='https://broker.example';
  let completeInvite;
  closedInvite.setHandler(()=>new Promise(resolve=>{completeInvite=resolve;}));
  const invitationPending=closedInvite.ids['invite-form'].fire('submit');await flush();
  closedInvite.ids['invite-dialog'].close();
  completeInvite({ok:true,invite:'late-secret',expires_at:Date.now()/1000+3600});await invitationPending;
  assert.equal(closedInvite.ids['invite-output'].textContent,'','late invitation response cannot restore a cleared secret');
  assert.equal(closedInvite.ids['invite-result'].hidden,true);
  console.log('ok closing invitation dialog discards in-flight invitation credentials');

  const empty=page({snapshot:{...fixture(),buses:[{name:'general',agents:[]}]}});await flush();
  assert.equal(empty.ids['empty-title'].textContent,'No published agents');
  assert.equal(empty.ids['empty-register'].hidden,false);
  const allNamed=page({snapshot:{...fixture(),buses:[{name:'all',agents:[fixture().buses[0].agents[0]]}]}});await flush();
  await allNamed.ids['bus-nav'].children[1].fire('click');
  assert.equal(allNamed.ids['page-title'].textContent,'all','a bus named all is distinct from aggregate view');
  console.log('ok empty state and bus name collision handling');

  const late=page();await flush();
  let release;
  late.setHandler(()=>new Promise(resolve=>{release=resolve;}));
  const pending=late.ids.refresh.fire('click');await flush();
  await late.ids.disconnect.fire('click');release(fixture());await pending;
  assert.equal(late.ids.app.hidden,true,'an in-flight response cannot restore a disconnected session');
  assert.equal(late.storage.size,0);
  console.log('ok disconnect ignores an in-flight snapshot');

  const browserToken='web1.'+'b'.repeat(43);
  const readerSnapshot={...fixture(),is_admin:false,principals:undefined,browser_session:true,read_only:true,logout_url:'/_gateway/logout'};
  const browser=page({hash:'',snapshot:readerSnapshot,bootstrap:{ok:true,token:browserToken,browser_session:true,logout_url:'/_gateway/logout'}});
  await flush();
  assert.equal(browser.requests[0].url,'/_bus/session');
  assert.equal(browser.requests[0].options.credentials,'same-origin');
  assert.equal(browser.requests[1].options.headers.Authorization,'Bearer '+browserToken);
  assert.equal(browser.ids.app.hidden,false);
  assert.equal(browser.ids.role.textContent,'Directory reader');
  assert.equal(browser.storage.get('communicate.bus.dashboard.token'),browserToken);
  assert.equal(browser.ids['login-token'].disabled,true);
  await browser.ids.disconnect.fire('click');
  assert.equal(browser.requests.at(-1).url,'/_gateway/logout');
  assert.equal(browser.requests.at(-1).options.method,'POST');
  assert.equal(browser.requests.at(-1).options.credentials,'same-origin');
  assert.deepEqual(browser.navigations,['/']);
  assert.equal(browser.storage.size,0);
  console.log('ok reader bootstrap, same-origin cookie use, read-only role, and gateway logout');

  const recovery=page({snapshot:readerSnapshot});await flush();
  let refreshedToken=false;
  recovery.setHandler(request=>{
    if (request.url==='/_bus/session') {refreshedToken=true;return {ok:true,token:browserToken,browser_session:true};}
    return refreshedToken ? readerSnapshot : {ok:false,status:401,error:'Password changed'};
  });
  await recovery.ids.refresh.fire('click');
  assert.equal(recovery.ids.app.hidden,false);
  assert.equal(recovery.requests.filter(request=>request.url==='/_bus/session').length,1);
  assert.equal(recovery.requests.at(-1).options.headers.Authorization,'Bearer '+browserToken);
  recovery.setHandler(request=>request.url==='/_bus/session' ? {ok:true,token:browserToken,browser_session:true} : {ok:false,status:401,error:'Reader revoked'});
  await recovery.ids.refresh.fire('click');
  assert.equal(recovery.ids.app.hidden,true);
  assert.equal(recovery.storage.size,0);
  const requestCount=recovery.requests.length;await recovery.timers[0]();await flush();
  assert.equal(recovery.requests.length,requestCount,'failed bootstrap recovery does not loop through polling');
  console.log('ok password-change recovery retries bootstrap once and stops on repeated denial');

  const local=page({hash:''});await flush();
  assert.equal(local.requests[0].url,'/_bus/session');
  assert.equal(local.ids.login.hidden,false);
  assert.equal(local.ids['login-token'].disabled,false);
  assert.equal(local.ids['login-submit'].hidden,false);
  console.log('ok local broker without browser bootstrap retains token login');

  const identity=page();await flush();
  const researchRow=identity.ids['agent-rows'].children[0];
  assert.equal(researchRow.children[1].textContent,'aadarwal','ownership gets its own desktop column');
  assert.ok(researchRow.children[2].textContent.includes('aadarwal-mini'));
  const mobileMetadata=researchRow.children[0].children[0].children[1].children.find(child=>child.className==='agent-location');
  assert.ok(mobileMetadata.textContent.includes('Useraadarwal'));
  assert.ok(mobileMetadata.textContent.includes('Devicelab-mac'));
  assert.ok(mobileMetadata.textContent.includes('Tailscale: aadarwal-mini'));
  identity.ids['user-filter'].value='user:peer';await identity.ids['user-filter'].fire('change');
  assert.equal(identity.ids['agent-rows'].children.length,1);
  assert.ok(identity.ids['agent-rows'].textContent.includes('builder'));
  assert.equal(identity.ids['live-count'].textContent,'0');assert.equal(identity.ids['queueable-count'].textContent,'1');
  assert.equal(identity.ids['roster-count'].textContent,'1 agent');
  assert.equal(identity.ids['device-filter'].children.length,2,'device choices follow the selected user');
  identity.ids['user-filter'].value='';await identity.ids['user-filter'].fire('change');
  identity.ids['device-filter'].value='device-a';await identity.ids['device-filter'].fire('change');
  assert.equal(identity.ids['agent-rows'].children.length,1);
  identity.ids.search.value='example.ts.net';await identity.ids.search.fire('input');
  assert.equal(identity.ids['agent-rows'].children.length,1,'Tailscale DNS identity is searchable');
  identity.ids.search.value='';await identity.ids.search.fire('input');
  identity.ids['user-filter'].value='unassigned:';await identity.ids['user-filter'].fire('change');
  assert.equal(identity.ids['agent-rows'].children.length,1);
  assert.equal(identity.ids['agent-rows'].children[0].children[1].textContent,'Unassigned','legacy ownership is never inferred from hostname');
  console.log('ok user/device columns, mobile identity metadata, owner filters, and Tailscale search');

  const duplicateFixture=fixture();
  duplicateFixture.buses[0].agents.push({...duplicateFixture.buses[0].agents[0],id:'same-label',name:'second-lab',device_id:'device-distinct',buses:['general']});
  duplicateFixture.buses[0].agents.push({...duplicateFixture.buses[0].agents[0],id:'same-device',name:'second-session',buses:['general']});
  const duplicates=page({snapshot:duplicateFixture});await flush();
  assert.equal(duplicates.ids['device-count'].textContent,'4','count stable device identities, not names or agent sessions');
  duplicates.ids['user-filter'].value='user:aadarwal';await duplicates.ids['user-filter'].fire('change');
  assert.equal(duplicates.ids['device-filter'].children.length,3);
  const labels=duplicates.ids['device-filter'].children.slice(1).map(option=>option.textContent);
  assert.notEqual(labels[0],labels[1],'identically named devices remain distinguishable');
  duplicates.ids['device-filter'].value='device-distinct';await duplicates.ids['device-filter'].fire('change');
  assert.equal(duplicates.ids['agent-rows'].children.length,1);
  assert.ok(duplicates.ids['agent-rows'].textContent.includes('second-lab'));
  console.log('ok duplicate device names and multiple sessions retain stable identities');

  const hosted=page({snapshot:{...fixture(),users:[{id:'aadarwal'},{id:'peer'}]}});await flush();
  await hosted.ids['invite-button'].fire('click');
  assert.equal(hosted.ids['invite-user-field'].hidden,false);assert.equal(hosted.ids['invite-user'].required,true);
  hosted.ids['invite-url'].value='https://bus.nonlocally.org';await hosted.ids['invite-url'].fire('input');
  await hosted.ids['invite-form'].fire('submit');
  assert.ok(hosted.ids['invite-error'].textContent.includes('Choose the user'));
  assert.equal(hosted.requests.filter(request=>request.op==='invite').length,0);
  hosted.ids['invite-user'].value='unexpected';await hosted.ids['invite-form'].fire('submit');
  assert.equal(hosted.requests.filter(request=>request.op==='invite').length,0);
  hosted.ids['invite-user'].value='peer';
  hosted.setHandler(request=>request.op==='invite' ? {ok:true,invite:'peer-device-code',expires_at:Date.now()/1000+3600} : fixture());
  await hosted.ids['invite-form'].fire('submit');
  assert.equal(hosted.requests.at(-1).user,'peer');
  assert.ok(hosted.ids['invite-expiry'].textContent.includes('peer · general'));
  assert.equal(i.ids['invite-user-field'].hidden,true,'standalone invitations retain their existing no-user flow');
  console.log('ok hosted invitation requires an explicitly configured recipient');

  const enrolledOnly=page({snapshot:{...fixture(),buses:[{name:'general',agents:[]}],principals:[{id:'p-enrolled',user:'peer',device:'workstation',device_id:'p-enrolled',device_metadata:{hostname:'peer-pc',tailscale_hostname:'peer-workstation'},buses:['general'],revoked:false}]}});await flush();
  assert.ok(enrolledOnly.ids['empty-description'].textContent.includes('1 device is enrolled, but no agents are published here'));
  assert.ok(enrolledOnly.ids['empty-description'].textContent.includes('Publish only the agents you want others to discover remotely'));
  assert.equal(enrolledOnly.ids['empty-invite'].hidden,false);
  await enrolledOnly.ids['devices-button'].fire('click');
  assert.ok(enrolledOnly.ids['device-list'].textContent.includes('User: peer'));
  assert.ok(enrolledOnly.ids['device-list'].textContent.includes('Tailscale: peer-workstation'));
  assert.equal(enrolledOnly.ids['device-count'].textContent,'0','enrolled devices are distinguished from devices with published recipients');
  console.log('ok empty roster explains enrollment and Devices shows account ownership');

  const emptyUsers=page({snapshot:{...fixture(),users:[{id:'aadarwal'},{id:'peer'}],buses:[{name:'general',agents:[]}],principals:[{id:'air-empty',device_id:'air-empty',user:'aadarwal',device:'Air',buses:['general'],revoked:false}]}});await flush();
  assert.deepEqual(emptyUsers.ids['user-filter'].children.map(option=>option.textContent),['All users','aadarwal','peer']);
  emptyUsers.ids['user-filter'].value='user:peer';await emptyUsers.ids['user-filter'].fire('change');
  assert.equal(emptyUsers.ids['user-filter'].value,'user:peer');
  assert.equal(emptyUsers.ids['empty-title'].textContent,'No published agents for this user');
  assert.ok(emptyUsers.ids['empty-description'].textContent.includes('No published agents are visible for peer'));
  await emptyUsers.ids['empty-invite'].fire('click');assert.equal(emptyUsers.ids['invite-user'].value,'peer');
  emptyUsers.ids['invite-dialog'].close();
  emptyUsers.ids['user-filter'].value='user:aadarwal';await emptyUsers.ids['user-filter'].fire('change');
  assert.ok(emptyUsers.ids['device-filter'].children.some(option=>option.value==='air-empty' && option.textContent.includes('none published')));
  emptyUsers.ids['device-filter'].value='air-empty';await emptyUsers.ids['device-filter'].fire('change');
  assert.equal(emptyUsers.ids['empty-title'].textContent,'No published agents on this device');
  assert.ok(emptyUsers.ids['empty-description'].textContent.includes('1 device is enrolled'));
  console.log('ok configured users and enrolled devices stay visible without published agents');

  const model=page();await flush();
  assert.equal(model.ids['roster-title'].textContent,'Published agents');
  assert.ok(model.ids['directory-note'].textContent.includes('Local Claude and Codex agents remain reachable automatically'));
  await model.ids['register-button'].fire('click');
  assert.equal(model.ids['register-phrase'].textContent,'Register yourself on the bus.','preserve the user-facing registration instruction');
  assert.equal(model.ids['register-output'].textContent,'communicate bus register');
  assert.ok(model.ids['register-intro'].textContent.includes('already reachable on the same device'));
  assert.ok(model.ids['register-access'].textContent.includes('without publishing itself'));
  assert.ok(model.ids['register-access'].textContent.includes('reply within that conversation'));
  assert.ok(model.ids['register-scope'].textContent.includes('admitted users and enrolled devices'));
  model.ids['register-dialog'].close();
  await model.ids['bus-nav'].children[2].fire('click');
  assert.equal(model.ids['roster-title'].textContent,'Joined agents');
  await model.ids['register-button'].fire('click');
  assert.equal(model.ids['register-phrase'].textContent,'Register yourself on the photonics bus.');
  assert.equal(model.ids['register-output'].textContent,'communicate bus register --bus photonics');
  assert.ok(model.ids['register-access'].textContent.includes('Both devices need invitations'));
  assert.ok(model.ids['register-access'].textContent.includes('both agents must explicitly join'));
  model.ids['register-dialog'].close();
  await model.ids['invite-button'].fire('click');
  assert.ok(model.ids['invite-access'].textContent.includes('both agents must explicitly join'));
  model.ids['invite-bus'].value='general';await model.ids['invite-bus'].fire('change');
  assert.ok(model.ids['invite-access'].textContent.includes('without publishing themselves'));
  const emptyPrivate=page({snapshot:{...fixture(),buses:[{name:'photonics',agents:[]}]}});await flush();
  await emptyPrivate.ids['bus-nav'].children[1].fire('click');
  assert.equal(emptyPrivate.ids['empty-title'].textContent,'No joined agents');
  assert.ok(emptyPrivate.ids['empty-description'].textContent.includes('both agents explicitly join this private bus'));
  console.log('ok automatic local reachability, general recipient publication, and explicit private membership copy');

  const graphCalls=[];
  let graphClears=0;
  const graphPage=page({pathname:'/graph',graphRenderer:{mount:()=>({update:data=>graphCalls.push(data),clear:()=>graphClears++,destroy(){}})}});await flush();
  assert.equal(graphPage.ids['graph-panel'].hidden,false);
  assert.equal(graphPage.ids['table-wrap'].hidden,true);
  assert.equal(graphPage.ids['view-graph'].attrs['aria-current'],'page');
  assert.equal(graphCalls.at(-1).agents.length,3);
  assert.deepEqual(Object.keys(graphCalls.at(-1)).sort(),['agents','buses','chat','scope','standalone']);
  assert.equal(graphCalls.at(-1).standalone,true);
  assert.equal(graphPage.ids.app.dataset.page,'graph');
  assert.equal(graphPage.ids['graph-topbar'].hidden,false);
  assert.ok(!JSON.stringify(graphCalls).includes('secret'),'graph gets no browser token');
  await graphPage.ids['bus-nav'].children[2].fire('click');
  assert.equal(graphCalls.at(-1).buses.length,1);
  assert.equal(graphCalls.at(-1).buses[0].name,'photonics');
  assert.equal(graphCalls.at(-1).agents.length,2);
  graphPage.ids.search.value='guest';await graphPage.ids.search.fire('input');
  assert.equal(graphCalls.at(-1).agents.length,1);
  assert.equal(graphPage.ids['graph-back'].attrs.href,'/?bus=photonics');
  const graphScope=graphCalls.at(-1).scope;
  graphPage.setHandler(()=>({ok:true,...fixture(),buses:[fixture().buses[0]]}));
  await graphPage.ids.refresh.fire('click');
  assert.notEqual(graphCalls.at(-1).scope,graphScope,'admission changes reset graph scope');
  assert.equal(graphCalls.at(-1).buses[0].name,'general');
  graphPage.setHandler(()=>({ok:false,status:401,error:'Revoked'}));
  await graphPage.ids.refresh.fire('click');
  assert.ok(graphClears>0,'revocation clears graph memory');
  assert.equal(graphPage.ids['graph-panel'].hidden,true);
  assert.equal(graphPage.ids.app.hidden,true);
  const missingGraph=page({pathname:'/graph'});await flush();
  assert.equal(missingGraph.ids['graph-unavailable'].hidden,false,'missing graph assets retain functional list');
  const brokenGraph=page({pathname:'/graph',graphRenderer:{mount:()=>({update(){},clear(){throw new Error('fixture');},destroy(){throw new Error('fixture');}})}});await flush();
  brokenGraph.setHandler(()=>({ok:false,status:401,error:'Revoked'}));
  await brokenGraph.ids.refresh.fire('click');
  assert.equal(brokenGraph.ids.app.hidden,true,'widget errors must not interrupt access revocation');
  assert.equal(brokenGraph.ids['graph-panel'].children.length,0);
  const direct=page({pathname:'/graph',search:'?bus=photonics',graphRenderer:{mount:()=>({update(){},clear(){},destroy(){}})}});await flush();
  assert.equal(direct.ids['graph-bus'].value,'photonics');
  assert.equal(direct.ids['agent-rows'].children.length,0,'standalone graph does not rebuild a hidden table on every poll');
  assert.equal(direct.ids['roster-count'].textContent,'2 agents');
  assert.equal(direct.ids['graph-back'].attrs.href,'/?bus=photonics');
  direct.ids['graph-bus'].value='general';await direct.ids['graph-bus'].fire('change');
  assert.equal(direct.history.at(-1),'/graph?bus=general');
  await direct.ids['graph-filter-toggle'].fire('click');
  assert.equal(direct.ids['graph-filter-panel'].hidden,false);
  const deniedBus=page({pathname:'/graph',search:'?bus=secret-not-admitted',graphRenderer:{mount:()=>({update(){},clear(){},destroy(){}})}});await flush();
  assert.equal(deniedBus.ids['graph-bus'].value,'');
  assert.equal(deniedBus.history.at(-1),'/graph');
  const directory=page({search:'?bus=photonics'});await flush();
  assert.equal(directory.ids['view-graph'].attrs.href,'/graph?bus=photonics');
  assert.equal(directory.ids['table-wrap'].hidden,false);
  assert.equal(directory.ids['graph-panel'].hidden,true);
  console.log('ok standalone graph links, permitted URL selection, filters, no credentials, revocation clear and asset fallback');
  function chatFixture() {return {...fixture(),is_admin:false,browser_session:true,read_only:true,user:'aadarsh',chat:{enabled:true,buses:['general','photonics'],openwebui:[{bus:'general',agent:'a'},{bus:'photonics',agent:'a'}]}};}
  function summary(id='chat-a',agent=fixture().buses[0].agents[0],bus='general') {return {id,bus,agent,unread:1,can_send:true,created_at:1,updated_at:2};}
  function message(seq,role='assistant',content='<img src=x onerror=alert(1)>',status='replied') {return {id:'m'+seq,seq,role,content,status,created_at:2};}
  function children(root) {return [root,...root.children.flatMap(children)];}
  const own=page({snapshot:chatFixture()});
  let ownMessages=[message(1)],ownChats=[summary()];
  own.setHandler(request => {
    if (request.op==='snapshot') return chatFixture();
    if (request.op==='chat_list') return {ok:true,chats:ownChats};
    if (request.op==='chat_open') return {ok:true,chat:ownChats[0]};
    if (request.op==='chat_messages') return {ok:true,chat:ownChats[0],messages:ownMessages.filter(m=>m.seq>request.after),next_after:ownMessages.at(-1)?.seq || 0,has_more:false};
    if (request.op==='chat_read') return {ok:true,unread:0};
    if (request.op==='chat_send') {const sent={...message(ownMessages.length+1,'user',request.message,'queued'),request_id:request.request_id};ownMessages.push(sent);return {ok:true,chat:'chat-a',message:sent,deduplicated:false};}
    throw new Error('unexpected operation '+request.op);
  });
  await flush();assert.equal(own.ids['inbox-button'].hidden,false);
  assert.equal(own.ids['inbox-button'].textContent,'Inbox · 1');
  await own.ids['inbox-button'].fire('click');await flush();
  assert.equal(own.ids['chat-dialog'].open,true);
  assert.equal(own.ids['chat-inbox'].children.length,1);
  await own.ids['chat-inbox'].children[0].fire('click');await flush();
  assert.equal(own.ids['chat-form'].hidden,false,'allowlisted directory reader can chat without gaining admin rights');
  assert.equal(own.ids['invite-button'].hidden,true);
  assert.ok(own.ids['chat-messages'].textContent.includes('<img src=x onerror=alert(1)>'));
  assert.equal(own.ids['chat-registration'].textContent,'Agent ID: a');
  assert.equal(own.ids['chat-device-id'].textContent,'Device ID: device-a');
  assert.equal(own.ids['chat-nonlocally'].attrs.href,'https://mit.nonlocally.org/?models=communicate_bus.general--a');
  assert.ok(own.requests.some(r=>r.op==='chat_read' && r.through===1));
  assert.ok(!own.requests.some(r=>r.op==='chat_send'),'opening inbox/history does not send');
  own.ids['chat-input'].value='Hello exact agent';await own.ids['chat-input'].fire('input');
  await own.ids['chat-form'].fire('submit');
  assert.equal(own.requests.at(-1).op,'chat_send');assert.match(own.requests.at(-1).request_id,/^[a-f0-9-]{36}$/);
  assert.equal(own.requests.at(-1).chat,'chat-a');
  assert.ok(own.ids['chat-messages'].textContent.includes('Queued for agent · awaiting reply'));
  assert.equal(own.ids['chat-input'].value,'');
  assert.ok(!own.requests.some(r=>Object.hasOwn(r,'sender')),'human identity is assigned by broker');
  console.log('ok human allowlist, own inbox, exact identity/deep-link, plain text, read acknowledgment, and honest queued receipt');

  const retry=page({snapshot:chatFixture()});let retryRequests=[];
  retry.setHandler(request => {
    if (request.op==='snapshot') return chatFixture();
    if (request.op==='chat_list') return {ok:true,chats:[summary()]};
    if (request.op==='chat_messages') return {ok:true,chat:summary(),messages:[],next_after:0,has_more:false};
    if (request.op==='chat_send') {retryRequests.push(request);return retryRequests.length===1 ? {ok:false,status:503,error:'Connection interrupted'} : {ok:true,chat:'chat-a',message:message(1,'user',request.message,'accepted'),deduplicated:true};}
    return {ok:true};
  });await flush();await retry.ids['inbox-button'].fire('click');await flush();await retry.ids['chat-inbox'].children[0].fire('click');
  retry.ids['chat-input'].value='One intentional message';await retry.ids['chat-form'].fire('submit');
  assert.equal(retry.ids['chat-input'].value,'One intentional message');assert.equal(retry.ids['chat-retry'].hidden,false);
  await retry.ids['chat-retry'].fire('click');assert.equal(retryRequests[0].request_id,retryRequests[1].request_id);
  retry.ids['chat-input'].value='One intentional message';await retry.ids['chat-form'].fire('submit');
  assert.notEqual(retryRequests[1].request_id,retryRequests[2].request_id,'a later intentional identical message gets a fresh request id');
  console.log('ok uncertain-send retry retains idempotency key; repeated intentional messages stay distinct');

  const selection=page({snapshot:chatFixture()});let finishOld,oldSignal;
  const second=summary('chat-b',fixture().buses[0].agents[1]);
  selection.setHandler(request => {
    if (request.op==='snapshot') return chatFixture();
    if (request.op==='chat_list') return {ok:true,chats:[summary(),second]};
    if (request.op==='chat_messages' && request.chat==='chat-a') {oldSignal=request.options.signal;return new Promise(resolve=>finishOld=resolve);}
    if (request.op==='chat_messages') return {ok:true,chat:second,messages:[message(1,'assistant','Current conversation')],next_after:1,has_more:false};
    return {ok:true,unread:0};
  });await flush();await selection.ids['inbox-button'].fire('click');await flush();
  const pendingOld=selection.ids['chat-inbox'].children[0].fire('click');await flush();
  await selection.ids['chat-back'].fire('click');await flush();
  await selection.ids['chat-inbox'].children[1].fire('click');await flush();assert.equal(oldSignal.aborted,true);
  finishOld({ok:true,chat:summary(),messages:[message(1,'assistant','STALE PRIVATE HISTORY')],next_after:1,has_more:false});await pendingOld;await flush();
  assert.ok(!selection.ids['chat-messages'].textContent.includes('STALE'));assert.ok(selection.ids['chat-messages'].textContent.includes('Current conversation'));
  let completeRevoked;selection.setHandler(request => request.op==='chat_messages' ? new Promise(resolve=>completeRevoked=resolve) : request.op==='snapshot' ? {...chatFixture(),chat:{enabled:false,buses:[]}} : {ok:true,chats:[second]});
  const pendingRevoked=selection.ids['chat-retry'].fire('click');await flush();await selection.ids.refresh.fire('click');
  assert.equal(selection.ids['chat-dialog'].open,false);assert.equal(selection.ids['chat-messages'].textContent,'');assert.equal(selection.ids['chat-form'].hidden,true);
  assert.equal(selection.ids['chat-registration'].textContent,'');assert.equal(selection.ids['chat-nonlocally'].attrs.href,'');
  completeRevoked({ok:true,chat:second,messages:[message(2,'assistant','REVOKED HISTORY')],next_after:2,has_more:false});await pendingRevoked;
  assert.equal(selection.ids['chat-messages'].textContent,'');assert.equal(selection.ids['inbox-button'].hidden,true);
  console.log('ok switching conversations and scope revocation abort requests and reject late private responses');

  const deviceToken=page({snapshot:{...chatFixture(),browser_session:false}});await flush();
  assert.equal(deviceToken.ids['inbox-button'].hidden,true);assert.equal(deviceToken.ids['chat-form'].hidden,true);
  assert.ok(!deviceToken.requests.some(r=>r.op.startsWith('chat_')),'device tokens never access human chat');
  const otherHuman=page({snapshot:{...chatFixture(),user:'another-reader',chat:{enabled:false,buses:[]}}});await flush();
  assert.equal(otherHuman.ids['inbox-button'].hidden,true);
  assert.ok(!otherHuman.requests.some(r=>r.op.startsWith('chat_')),'non-allowlisted readers stay directory-only');
  const multi=page({snapshot:chatFixture()});multi.setHandler(r=>r.op==='snapshot' ? chatFixture() : {ok:true,chats:[]});await flush();
  const multiButton=children(multi.ids['agent-rows']).find(el=>el.attrs['aria-label']==='Chat with research');await multiButton.fire('click');
  assert.equal(multi.ids['chat-bus-field'].hidden,false);assert.equal(multi.ids['chat-start'].hidden,false);
  assert.ok(!multi.requests.some(r=>r.op==='chat_open'),'multiple eligible buses require a visible bus choice before opening');
  multi.ids['chat-bus'].value='photonics';await multi.ids['chat-bus'].fire('change');
  assert.equal(multi.ids['chat-nonlocally'].attrs.href,'https://mit.nonlocally.org/?models=communicate_bus.photonics--a');
  const unconfiguredData={...chatFixture(),chat:{enabled:true,buses:['general','photonics']}};
  const unconfigured=page({snapshot:unconfiguredData});unconfigured.setHandler(r=>r.op==='snapshot' ? unconfiguredData : {ok:true,chats:[]});await flush();
  await children(unconfigured.ids['agent-rows']).find(el=>el.attrs['aria-label']==='Chat with research').fire('click');
  assert.equal(unconfigured.ids['chat-nonlocally'].hidden,true,'missing deployed Pipe catalog defaults to no external link');
  assert.equal(unconfigured.ids['chat-start'].hidden,false,'native chat remains available before Pipe deployment');
  multi.setHandler(r=>r.op==='snapshot' ? {...chatFixture(),chat:{...chatFixture().chat,openwebui:[{bus:'general',agent:'b'}]}} : {ok:false,status:503,error:'Inbox unavailable'});
  await multi.ids.refresh.fire('click');await flush();
  assert.equal(multi.ids['chat-nonlocally'].hidden,true,'catalog removal takes effect before a successful inbox poll');
  console.log('ok human-only permission gate, explicit multi-bus choice, and default-deny exact deployed Pipe catalog');
})().catch(error=>{console.error(error);process.exitCode=1;});
