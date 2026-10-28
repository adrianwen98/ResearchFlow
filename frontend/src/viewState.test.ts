import { expect, it } from "vitest";
import { readTaskView, saveTaskView } from "./viewState";
import { RequestScope } from "./requestScope";

function memoryStorage() {
  const values = new Map<string,string>();
  return {getItem:(key:string)=>values.get(key) ?? null, setItem:(key:string,value:string)=>{values.set(key,value);}};
}
it("restores each task's own preview, workbook and sheet after reload",()=>{
  const storage=memoryStorage();
  saveTaskView("A",{open:true,tab:"data",file:"task_result.xlsx",sheet:1},storage);
  saveTaskView("B",{open:true,tab:"data",file:"research_sources.xlsx",sheet:0},storage);
  expect(readTaskView("A",storage)).toEqual({open:true,tab:"data",file:"task_result.xlsx",sheet:1});
  expect(readTaskView("B",storage)).toEqual({open:true,tab:"data",file:"research_sources.xlsx",sheet:0});
  saveTaskView("A",{open:false,tab:"execution",file:"task_result.xlsx",sheet:1},storage);
  expect(readTaskView("A",storage).open).toBe(false);
  expect(readTaskView("B",storage).open).toBe(true);
  expect(readTaskView("new-task",storage).open).toBe(false);
});
it("recovers from damaged or unavailable storage and ignores invalid file/sheet/tab values",()=>{
  const storage=memoryStorage();
  storage.setItem("webtask:view:v1:broken","{");
  expect(readTaskView("broken",storage).tab).toBe("report");
  storage.setItem("webtask:view:v1:bad",JSON.stringify({open:"true",tab:"fake",file:"../private.xlsx",sheet:999}));
  expect(readTaskView("bad",storage)).toEqual({open:false,tab:"report",file:"task_result.xlsx",sheet:0});
  const unavailable={getItem:()=>{throw new Error("blocked");},setItem:()=>{throw new Error("blocked");}};
  expect(readTaskView("A",unavailable).open).toBe(false);
  expect(()=>saveTaskView("A",readTaskView("A",storage),unavailable)).not.toThrow();
});
it("rejects a delayed A response after selecting B and returning to A",async()=>{
  const scope=new RequestScope();
  let finishOld!: (value:string)=>void;
  const response=new Promise<string>(resolve=>{finishOld=resolve;});
  const ownsOldA=scope.capture();
  let shown="A loading";
  const oldA=response.then(value=>{if(ownsOldA())shown=value;});
  scope.invalidate();shown="B current";
  scope.invalidate();const ownsNewA=scope.capture();
  if(ownsNewA())shown="A current";
  finishOld("A obsolete response");await oldA;
  expect(shown).toBe("A current");expect(ownsNewA()).toBe(true);
});
it("invalidates delayed workbook errors when another workbook or task is selected",async()=>{
  const scope=new RequestScope();const ownsOld=scope.capture();
  let fail!: (reason:Error)=>void;
  const response=new Promise<never>((_,reject)=>{fail=reject;});
  let error="";
  const old=response.catch(e=>{if(ownsOld())error=e.message;});
  scope.invalidate();fail(new Error("Previous workbook missing"));await old;
  expect(error).toBe("");
});
