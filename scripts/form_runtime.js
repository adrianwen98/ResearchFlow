// Host-owned implementation. spec is JSON data inserted by the host, never JS from the model.
async function webtaskForm(spec) {
  const form = document.forms[spec.form_index];
  if (!form) throw new Error('Form index not found');
  const controls = Array.from(form.elements).filter(e => e.name && !e.disabled && !['submit','button','reset'].includes(e.type));
  const allowed = new Set(['text','textarea','email','url','search','tel','number','select-one']);
  if (controls.some(e => !allowed.has(e.type))) throw new Error('Unsupported/hidden/sensitive control');
  const names = controls.map(e => e.name);
  if (new Set(names).size !== names.length) throw new Error('Duplicate field names');
  if (Object.keys(spec.fields).some(name => !names.includes(name))) throw new Error('Unknown field');
  if (controls.some(e => e.required && !String(spec.fields[e.name] || '').trim())) throw new Error('Required field missing');
  for (const e of controls) {
    const value = String(spec.fields[e.name] || '');
    if (e.type === 'select-one' && value && !Array.from(e.options).some(o => o.value === value)) throw new Error('Invalid option');
  }
  const descriptor = {page_url:location.href, action:new URL(form.getAttribute('action') || location.href,location.href).href,
    method:form.method.toUpperCase(), enctype:form.enctype, title:document.title,
    text:form.innerText.slice(0,800), controls:controls.map(e => ({name:e.name,type:e.type,required:e.required,
      maxLength:e.maxLength,pattern:e.pattern || '',options:e.options ? Array.from(e.options).map(o=>o.value) : []}))};
  const descriptor_json = JSON.stringify(descriptor);
  const body = new URLSearchParams(Object.entries(spec.fields)).toString();
  if (spec.mode === 'inspect') return {descriptor,descriptor_json,body};
  if (spec.mode !== 'submit' || descriptor_json !== spec.expected_descriptor_json) throw new Error('Form changed after approval');
  // No native handlers, cookies, hidden fields, implicit button values or redirects.
  const response = await fetch(descriptor.action,{method:'POST',credentials:'omit',redirect:'error',
    headers:{'Content-Type':'application/x-www-form-urlencoded;charset=UTF-8'},body});
  return {status:response.status,url:response.url,text:(await response.text()).slice(0,4000)};
}
