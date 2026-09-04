"use strict";
const assert=require("node:assert/strict");
const {parseQuickAdd,normalizeSearch,fuzzyRank,rankSearchItems}=require("../static/app.js");
const base=new Date(2026,8,4,12);
const parse=input=>parseQuickAdd(input,base);

assert.equal(parse("Einfacher Titel").title,"Einfacher Titel");
assert.equal(parse("Angebot @Müller").assignee,"Müller");
assert.deepEqual(parse("Angebot #Kunde #Wichtig").tags,["Kunde","Wichtig"]);
assert.equal(parse("Angebot !Netz").project,"Netz");
assert.equal(parse("Angebot %Vertrag").category,"Vertrag");
assert.equal(parse("Dokument https://example.com").link,"https://example.com");
assert.deepEqual(parse("Freigabe ^123").dependencies,[123]);
assert.deepEqual(parse("Freigabe ^123 ^127").dependencies,[123,127]);
assert.equal(parse("Termin +3").due_date,"2026-09-07");
assert.equal(parse("Termin +1w").due_date,"2026-09-11");
assert.equal(parse("Termin 15.10.").due_date,"2026-10-15");
assert.deepEqual(parse("#Wichtig @Meier Angebot prüfen +3"),{
  title:"Angebot prüfen",assignee:"Meier",due_date:"2026-09-07",tags:["Wichtig"],project:null,category:null,dependencies:[],link:null
});
assert.deepEqual(parse('Rückmeldung @"Max Mustermann" !"Projekt Nord" %"Interne Abstimmung" #"Sehr wichtig"'),{
  title:"Rückmeldung",assignee:"Max Mustermann",due_date:null,tags:["Sehr wichtig"],project:"Projekt Nord",category:"Interne Abstimmung",dependencies:[],link:null
});
assert.equal(parse("Präsentation für Vorstand überarbeiten @Meier +3 #Vorstand").title,"Präsentation für Vorstand überarbeiten");
assert.throws(()=>parse("@Meier #Tag"),/Aufgabentitel/);
assert.throws(()=>parse("Titel https://a.example https://b.example"),/einen Link/);
assert.throws(()=>parse('Titel @"Max Mustermann'),/nicht geschlossen/);
assert.throws(()=>parse("Termin +falsch"),/Datum nicht erkannt/);

assert.equal(normalizeSearch("Müllerstraße"),"mullerstrasse");
assert.ok(fuzzyRank("hist","Historie öffnen")<fuzzyRank("hist","Globale Historie"));
assert.ok(fuzzyRank("uberf","Nur überfällige Tasks anzeigen")>=0);
assert.ok(fuzzyRank("bkp","Backup erstellen")>=0);
const ranked=rankSearchItems("#123",[
  {type:"task",id:12,search:"#12 Titel 123"},{type:"task",id:123,search:"#123 Anderer Titel"},{type:"command",search:"123"}
]);
assert.equal(ranked[0].id,123);

console.log("JavaScript-Logiktests erfolgreich");
