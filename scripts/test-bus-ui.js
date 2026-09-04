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
    disabled:false, open:false, checked:false, required:false, _text:'', events:{},
    set textContent(value) {this.children=[];this._text=String(value);},
    get textContent() {return this._text+this.children.map(child=>child.textContent).join('');},
    set innerHTML(_) {throw new Error('Untrusted HTML must never be parsed');},
    setAttribute(name,value) {this.attrs[name]=String(value);},
    append(...children) {this.children.push(...children);},
    replaceChildren(...children) {this._text='';this.children=children;},
    addEventListener(name,handler) {(this.events[name] ||= []).push(handler);},
    async fire(name) {await Promise.all((this.events[name] || []).map(handler => handler({target:this,preventDefault(){}})));},
    showModal() {this.open=true;},
    close() {this.open=false;this.fire('close');},
  };
}
const flush = () => new Promise(resolve => setImmediate(() => setImmediate(resolve)));
function page({hash='#token=secret', storedToken='', snapshot=fixture(), origin='http://127.0.0.1:7331', bootstrap=null}={}) {
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
    document,URL,URLSearchParams,TextEncoder,AbortController,Map,Set,Date,
    location:{hash,pathname:'/',search:'',origin,assign:url=>navigations.push(url)},
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
  const claude={id:'a',name:'research',kind:'claude',device:'lab-mac',status:'live',last_seen:now,description:'Designing the next experiment',buses:['general','photonics']};
  const codex={id:'b',name:'builder',kind:'codex',device:'workstation',status:'queueable',last_seen:now-30,description:'Reviewing the simulation',buses:['general']};
  const unsafe={id:'c',name:'<img src=x onerror=alert(1)>',kind:'claude',device:'guest-laptop',status:'offline',last_seen:now-400,description:'<script>bad()</script>',buses:['photonics']};
  return {ok:true,server_id:'fixture',is_admin:true,buses:[{name:'general',visibility:'open',agents:[claude,codex]},{name:'photonics',visibility:'private',agents:[claude,unsafe]}],principals:[{id:'p1',device:'guest-laptop',buses:['photonics'],revoked:false}]};
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
  assert.equal(empty.ids['empty-title'].textContent,'No registered agents');
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
})().catch(error=>{console.error(error);process.exitCode=1;});
