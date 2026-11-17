import {createHash} from 'node:crypto';
export type Answer = string | number | boolean | (string | number | boolean)[];
export type Answers = Record<string, Answer>;
export interface Field {
  id: string; type: string; label: string; required: boolean; options?: string[];
  show_if?: {field: string; equals: Answer};
  verification?: {expected: Answer; mode: string; on_mismatch: string};
}
export interface Schema {title: string; description: string; confirmation_message: string; fields: Field[]}
export class ApiError extends Error {
  constructor(public status: number, message: string, public fields?: Record<string, string>) {super(message);}
}
export function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new ApiError(400, 'Expected an object.');
  return value as Record<string, unknown>;
}
function str(value: unknown, max: number, name: string, empty = false): string {
  if (typeof value !== 'string' || value.length > max || (!empty && !value.trim())) throw new ApiError(400, 'Invalid '+name+'.');
  return value;
}
export function stable(value: unknown, depth = 0): string {
  // RESPONSE-CARDS: valid schemas and answers are shallow; hostile JSON must not exhaust the Worker stack before validation.
  if (depth > 64) throw new ApiError(400, 'JSON nesting is too deep.');
  if (Array.isArray(value)) return '['+value.map(item=>stable(item,depth+1)).join(',')+']';
  if (value && typeof value === 'object') return '{'+Object.entries(value).sort(([a],[b])=>a.localeCompare(b)).map(([k,v])=>JSON.stringify(k)+':'+stable(v,depth+1)).join(',')+'}';
  return JSON.stringify(value);
}
function literal(value: unknown): value is Answer {
  return typeof value==='boolean' || (typeof value==='number' && Number.isFinite(value)) ||
    (typeof value==='string' && value.length<=10000) ||
    (Array.isArray(value) && value.length<=100 && value.every(v=>(typeof v==='string' && v.length<=10000) || typeof v==='boolean' || (typeof v==='number' && Number.isFinite(v))));
}
export function validateSchema(value: unknown): Schema {
  const s=object(value), fields: Field[]=[], ids=new Map<string,Field>();
  if (!Array.isArray(s.fields) || s.fields.length>50) throw new ApiError(400,'Use no more than 50 fields.');
  const types=new Set(['static','acknowledgment','short_text','long_text','single_select','multi_select','confirmation','boolean','integer','decimal','date','time','radio','attestation']);
  for (const raw of s.fields) {
    const f=object(raw), id=str(f.id,64,'field ID'), type=str(f.type,40,'field type');
    if (!/^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(id) || ['constructor','prototype','__proto__'].includes(id) || ids.has(id) || !types.has(type)) throw new ApiError(400,'Invalid field ID or type.');
    if (f.required!==undefined && typeof f.required!=='boolean') throw new ApiError(400,'Required must be boolean.');
    const field: Field={id,type,label:str(f.label,type==='static'?10000:2000,'field label'),required:f.required===true};
    if (['single_select','multi_select','radio'].includes(type)) {
      if (!Array.isArray(f.options) || !f.options.length || f.options.length>100 || f.options.some(o=>typeof o!=='string' || !o.trim() || o.length>200) || new Set(f.options).size!==f.options.length) throw new ApiError(400,'Invalid options.');
      field.options=f.options as string[];
    }
    if (f.show_if!==undefined) {
      const c=object(f.show_if), source=ids.get(String(c.field));
      if (!source || source.type==='static' || !literal(c.equals)) throw new ApiError(400,'Conditions must refer to an earlier response field.');
      field.show_if={field:source.id,equals:c.equals};
    }
    if (f.verification!==undefined) {
      const v=object(f.verification), mode=String(v.mode||'exact'), on_mismatch=String(v.on_mismatch||'ALLOW_AND_FLAG');
      if (!literal(v.expected) || !['exact','case-insensitive','normalized','numeric','one-of','compact','uin-digest'].includes(mode) || !['BLOCK_SUBMISSION','ALLOW_AND_FLAG','ALLOW'].includes(on_mismatch) || (mode==='one-of' && !Array.isArray(v.expected))) throw new ApiError(400,'Invalid verification.');
      if (mode==='compact' && typeof v.expected!=='string') throw new ApiError(400,'Invalid verification.');
      if (mode==='uin-digest' && !(typeof v.expected==='string' && /^[0-9a-f]{16}:[0-9a-f]{64}$/.test(v.expected))) throw new ApiError(400,'Invalid verification.');
      field.verification={expected:v.expected,mode,on_mismatch};
    }
    if (type==='static' && (field.required || field.verification)) throw new ApiError(400,'Information blocks cannot require answers.');
    fields.push(field); ids.set(id,field);
  }
  return {title:str(s.title,200,'title'),description:str(s.description??'',10000,'description',true),confirmation_message:str(s.confirmation_message??'Thank you. Your response has been recorded.',2000,'confirmation message',true),fields};
}
export function publicSchema(schema: Schema): Schema {
  return {...schema,fields:schema.fields.map(({verification,...field})=>field)};
}
function normalized(v: Answer): string {return String(v).normalize('NFKC').trim().replace(/\s+/gu,' ').toLocaleLowerCase('en-US');}
// Codes such as seats and rooms: letters and digits only, case-folded, so "hecc 105", "HECC-105" and "HECC105" agree.
function compact(v: Answer): string {return String(v).normalize('NFKC').toLocaleLowerCase('en-US').replace(/[^\p{L}\p{N}]+/gu,'');}
// Synchronous SHA-256 for the synchronous validator; node:crypto needs "nodejs_compat" in wrangler.jsonc, so keep that flag.
export function sha256Hex(text: string): string {return createHash('sha256').update(text).digest('hex');}
export function matches(value: Answer, expected: Answer, mode: string): boolean {
  switch(mode) {
    case 'compact': return compact(value)===compact(expected);
    case 'uin-digest': {
      const digits=String(value).replace(/\D+/gu,''), [salt,digest]=String(expected).split(':');
      return digits.length===9 && !!salt && !!digest && sha256Hex(salt+digits)===digest;
    }
    case 'case-insensitive': return String(value).toLocaleLowerCase('en-US')===String(expected).toLocaleLowerCase('en-US');
    case 'normalized': return normalized(value)===normalized(expected);
    case 'numeric': return !Array.isArray(value) && !Array.isArray(expected) && typeof value!=='boolean' && typeof expected!=='boolean' && String(value).trim()!=='' && String(expected).trim()!=='' && Number.isFinite(Number(value)) && Number(value)===Number(expected);
    case 'one-of': return Array.isArray(expected) && expected.some(v=>stable(value)===stable(v));
    default: return stable(value)===stable(expected);
  }
}
export const UIN_MARK='[UIN confirmed]';
export function validateAnswers(schema: Schema, raw: unknown, draft=false) {
  const input=object(raw), answers: Answers={}, errors: Record<string,string>={}, verification: Record<string,{matched:boolean;on_mismatch:string}>={};
  const known=new Set(schema.fields.map(f=>f.id));
  if (Object.keys(input).some(id=>!known.has(id))) throw new ApiError(400,'Unknown answer field.');
  for (const f of schema.fields) {
    if (f.type==='static' || (f.show_if && stable(answers[f.show_if.field])!==stable(f.show_if.equals))) continue;
    const value=input[f.id]??(f.type==='multi_select'?[]:undefined);
    const missing=value===undefined || value===null || value==='';
    if (missing) {if (f.required && !draft) errors[f.id]='This response is required.'; continue;}
    let valid: boolean;
    if (['acknowledgment','boolean'].includes(f.type)) valid=typeof value==='boolean';
    else if (['integer','decimal'].includes(f.type)) valid=typeof value==='number' && Number.isFinite(value) && (f.type!=='integer' || Number.isSafeInteger(value));
    else if (f.type==='multi_select') valid=Array.isArray(value) && value.every(v=>f.options!.includes(v)) && new Set(value).size===value.length;
    else valid=typeof value==='string' && value.length<=(f.type==='long_text'?10000:2000);
    if (valid && ['single_select','radio'].includes(f.type)) valid=f.options!.includes(String(value));
    if (valid && f.type==='date') valid=/^\d{4}-\d{2}-\d{2}$/.test(String(value)) && Number.isFinite(Date.parse(String(value)+'T00:00:00Z')) && new Date(String(value)+'T00:00:00Z').toISOString().startsWith(String(value));
    if (valid && f.type==='time') valid=/^(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$/.test(String(value));
    if (!valid) {errors[f.id]='Enter a valid response.';continue;}
    answers[f.id]=value as Answer;
    if (!draft && f.required && ((f.type==='acknowledgment' && value!==true) || (typeof value==='string' && !value.trim()) || (Array.isArray(value) && !value.length))) errors[f.id]='This response is required.';
    if (!draft && f.verification) {
      const matched=matches(value as Answer,f.verification.expected,f.verification.mode);
      verification[f.id]={matched,on_mismatch:f.verification.on_mismatch};
      if (!matched && f.verification.on_mismatch==='BLOCK_SUBMISSION') errors[f.id]=f.verification.mode==='uin-digest'?'Enter your own 9-digit UIN.':'This response does not match the expected information. Check it and try again.';
    }
    // A UIN is checked against its salted digest and never stored: drafts drop it, submissions keep only a marker.
    if (f.verification?.mode==='uin-digest') {if (draft) delete answers[f.id]; else answers[f.id]=UIN_MARK;}
  }
  if (Object.keys(errors).length) throw new ApiError(422,'Please check the highlighted responses.',errors);
  const checks=Object.values(verification), status=checks.some(v=>!v.matched && v.on_mismatch==='ALLOW_AND_FLAG')?'needs_review':checks.length && checks.every(v=>v.matched)?'verified':'submitted';
  return {answers,verification,status};
}
