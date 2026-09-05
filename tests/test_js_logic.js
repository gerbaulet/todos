"use strict";
const assert=require("node:assert/strict");
const {parseDate,parseDue,parseDependency,parseQuickAdd,moveDue,effectiveDueStart,timelineEntries,normalizeSearch,fuzzyRank,rankSearchItems}=require("../static/app.js");
const base=new Date(2026,8,4,12);
const parse=input=>parseQuickAdd(input,base);

assert.equal(parse("Einfacher Titel").title,"Einfacher Titel");
assert.equal(parse("Angebot @Müller").assignee,"Müller");
assert.deepEqual(parse("Angebot #Kunde #Wichtig").tags,["Kunde","Wichtig"]);
assert.equal(parse("Angebot !Netz").project,"Netz");
assert.equal(parse("Angebot %Vertrag").category,"Vertrag");
assert.equal(parse("Dokument https://example.com").link,"https://example.com");
assert.deepEqual(parse("Freigabe ^123").dependencies,[{depends_on_task_id:123,offset_value:null,offset_unit:null}]);
assert.deepEqual(parse("Freigabe ^123+2W ^127").dependencies,[{depends_on_task_id:123,offset_value:2,offset_unit:"week"},{depends_on_task_id:127,offset_value:null,offset_unit:null}]);
assert.equal(parse("Termin +3").due_value,"2026-09-07");
assert.equal(parse("Termin +1w").due_value,"2026-09-11");
assert.equal(parse("Termin 15.10.").due_value,"2026-10-15");
assert.deepEqual(parse("#Wichtig @Meier Angebot prüfen +3"),{
  title:"Angebot prüfen",assignee:"Meier",due_type:"exact",due_value:"2026-09-07",tags:["Wichtig"],project:null,category:null,dependencies:[],link:null,is_milestone:false
});
assert.deepEqual(parse('Rückmeldung @"Max Mustermann" !"Projekt Nord" %"Interne Abstimmung" #"Sehr wichtig"'),{
  title:"Rückmeldung",assignee:"Max Mustermann",due_type:null,due_value:null,tags:["Sehr wichtig"],project:"Projekt Nord",category:"Interne Abstimmung",dependencies:[],link:null,is_milestone:false
});
assert.equal(parse("Präsentation für Vorstand überarbeiten @Meier +3 #Vorstand").title,"Präsentation für Vorstand überarbeiten");
assert.throws(()=>parse("@Meier #Tag"),/Aufgabentitel/);
assert.throws(()=>parse("Titel https://a.example https://b.example"),/einen Link/);
assert.throws(()=>parse('Titel @"Max Mustermann'),/nicht geschlossen/);
assert.throws(()=>parse("Termin +falsch"),/Datum nicht erkannt/);

assert.deepEqual(parseDue("15.09.2027",base),{due_type:"exact",due_value:"2027-09-15"});
assert.deepEqual(parseDue("15.9.27",base),{due_type:"exact",due_value:"2027-09-15"});
assert.deepEqual(parseDue("KW 33",new Date(2026,0,2,12)),{due_type:"week",due_value:"2026-W33"});
assert.deepEqual(parseDue("KW33",new Date(2026,11,15,12)),{due_type:"week",due_value:"2027-W33"});
assert.deepEqual(parseDue("KW 33 2027",base),{due_type:"week",due_value:"2027-W33"});
assert.deepEqual(parseDue("KW33/2027",base),{due_type:"week",due_value:"2027-W33"});
assert.deepEqual(parseDue("September 2027",base),{due_type:"month",due_value:"2027-09"});
assert.deepEqual(parseDue("Sep 2027",base),{due_type:"month",due_value:"2027-09"});
assert.deepEqual(parseDue("09/2027",base),{due_type:"month",due_value:"2027-09"});
assert.deepEqual(parseDue("Q2 2027",base),{due_type:"quarter",due_value:"2027-Q2"});
assert.deepEqual(parseDue("Q2/2027",base),{due_type:"quarter",due_value:"2027-Q2"});
assert.deepEqual(parseDue("2027",base),{due_type:"year",due_value:"2027"});
assert.equal(parseDate("morgen",base),"2026-09-05");
assert.deepEqual(parse("Abnahme vorbereiten @Müller Q2 2027 ^123+2w"),{
  title:"Abnahme vorbereiten",assignee:"Müller",due_type:"quarter",due_value:"2027-Q2",tags:[],project:null,category:null,
  dependencies:[{depends_on_task_id:123,offset_value:2,offset_unit:"week"}],link:null,is_milestone:false
});
assert.equal(parse("* Freigabe Q4 2026").is_milestone,true);
assert.equal(parse("   * Freigabe Q4 2026").title,"Freigabe");
assert.equal(parse("Freigabe * Vorstand Q4 2026").is_milestone,false);
assert.equal(parse("Freigabe Q4 2026 *").is_milestone,false);
assert.throws(()=>parse("* Freigabe"),/eigenen Termin/);
assert.deepEqual(parse("* Freigabe KW 42 2026 @Müller ^123+2w"),{
  title:"Freigabe",assignee:"Müller",due_type:"week",due_value:"2026-W42",tags:[],project:null,category:null,
  dependencies:[{depends_on_task_id:123,offset_value:2,offset_unit:"week"}],link:null,is_milestone:true
});
assert.deepEqual(parseDependency("#123 +1M"),{depends_on_task_id:123,offset_value:1,offset_unit:"month"});
assert.deepEqual(moveDue({due_type:"week",due_start:"2027-08-16"},1),{due_type:"week",due_value:"2027-W34"});
assert.deepEqual(moveDue({due_type:"month",due_start:"2027-09-01"},1),{due_type:"month",due_value:"2027-10"});
assert.deepEqual(moveDue({due_type:"quarter",due_start:"2027-04-01"},1),{due_type:"quarter",due_value:"2027-Q3"});
assert.deepEqual(moveDue({due_type:"year",due_start:"2027-01-01"},1),{due_type:"year",due_value:"2028"});
const recommendedTask={id:7,title:"Kind",due_type:null,due_start:null,due_end:null,due_display:"",dependencies:[{depends_on_task_id:1,recommended_start:"2026-09-15",recommended_end:"2026-10-14"}]};
assert.equal(effectiveDueStart(recommendedTask),"2026-09-15");
assert.deepEqual(timelineEntries(recommendedTask).map(x=>[x.start,x.end,x.precision,x.recommended]),[["2026-09-15","2026-10-14","range",true]]);
const oneDayTask={...recommendedTask,dependencies:[{depends_on_task_id:1,recommended_start:"2026-09-15",recommended_end:"2026-09-15"}]};
assert.equal(timelineEntries(oneDayTask)[0].label,"15.09.2026");

assert.equal(normalizeSearch("Müllerstraße"),"mullerstrasse");
assert.ok(fuzzyRank("hist","Historie öffnen")<fuzzyRank("hist","Globale Historie"));
assert.ok(fuzzyRank("uberf","Nur überfällige Tasks anzeigen")>=0);
assert.ok(fuzzyRank("bkp","Backup erstellen")>=0);
const ranked=rankSearchItems("#123",[
  {type:"task",id:12,search:"#12 Titel 123"},{type:"task",id:123,search:"#123 Anderer Titel"},{type:"command",search:"123"}
]);
assert.equal(ranked[0].id,123);

console.log("JavaScript-Logiktests erfolgreich");
