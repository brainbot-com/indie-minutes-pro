# Vorlage: Datenschutz-Folgenabschätzung für Minutes Pro

Diese Vorlage hilft Unternehmen, die Minutes Pro einsetzen, bei der
Datenschutz-Folgenabschätzung nach Art. 35 DSGVO. Die technischen Angaben
beschreiben, wie Minutes Pro arbeitet; die Felder in eckigen Klammern füllt
der Verantwortliche aus. Sie ist keine Rechtsberatung: Prüfen Sie das Ergebnis
mit Ihrer oder Ihrem Datenschutzbeauftragten und, wo es einen gibt, mit dem
Betriebsrat. Englische Fassung: [dpia-template.md](dpia-template.md).

## 1. Rahmen

| | |
|---|---|
| Verantwortlicher | [Unternehmen, Anschrift] |
| Datenschutzbeauftragte(r) | [Name, Kontakt] |
| Verfahren | Aufnahme, Transkription und Zusammenfassung von Besprechungen mit Minutes Pro |
| Version, Betrieb | Minutes Pro [Version] auf [Indie Box / eigenem Server], im Netz [Standort] |
| Erstellt von, am | [Name], [Datum] |

## 2. Beschreibung der Verarbeitung

**Zweck.** [Beispiel: Protokolle von Projektbesprechungen, damit Entscheidungen
und Aufgaben festgehalten sind.]

**Ablauf.**
1. Eine dafür bestimmte Person (der Inhaber) startet in einer Besprechung auf
   dem Telefon eine Aufnahme. Minutes Pro nimmt nur auf, wenn jemand auf
   Aufnehmen tippt; es hört nicht von selbst mit.
2. Das Telefon verschlüsselt jedes Stück der Aufnahme (AES-256-GCM, Schlüssel
   aus ECDH P-256 mit dem Schlüssel der Box) und schickt es über HTTPS an die
   Box im eigenen Netz.
3. Auf der Box erkennt ein Sprachmodell (NVIDIA Nemotron 3.5 Transcribe) den
   Text und trennt die Sprecher ("Sprecher 1, 2 ..."); ein Sprachmodell der
   Box schreibt eine Zusammenfassung mit Entscheidungen und Aufgaben.
4. Der Inhaber liest das Ergebnis und kann den Sprechern Namen geben.
   Mitglieder seines Teams lesen die Besprechungen, die er mit dem Team teilt.

**Betroffene.** Alle, die in einer aufgenommenen Besprechung sprechen
(Beschäftigte, Kunden, Partner, Gäste); die Personen im Team.

**Daten.**

| Kategorie | Beispiele | Wo |
|---|---|---|
| Ton | die Aufnahme der Besprechung | Box, verschlüsselt |
| Inhalt | Transkript, Zusammenfassung, Aufgaben | Box, verschlüsselt |
| Zuordnung | Sprecherkennungen, vergebene Namen, kurze Hörproben (nur bei Bedarf erzeugt, nicht gespeichert) | Box, verschlüsselt |
| Konten | Name und Passwort-Hash der Team-Mitglieder, letzte Anmeldung; Telefone des Inhabers | Box, verschlüsselt |
| Besondere Kategorien (Art. 9) | können vorkommen, wenn in der Besprechung etwa über Gesundheit gesprochen wird | [Umgang festlegen] |

Minutes Pro legt keine Stimmprofile an: Sprecher werden je Besprechung
getrennt, nicht wiedererkannt.

**Empfänger.** Der Inhaber; Team-Mitglieder für geteilte Besprechungen. Nur
wenn der Inhaber es einschaltet: Google Drive für einzelne Besprechungen, und
ein eigener KI-Agent, der Zusammenfassungen verbessert. [Beides
ausgeschlossen / so geregelt: ...] Der Hersteller (brainbot) hat keinen
Zugriff.

**Speicherdauer.** Bis der Inhaber eine Besprechung löscht. [Frist festlegen,
zum Beispiel: Aufnahmen nach 90 Tagen löschen, Zusammenfassungen nach Ende des
Projekts.] Sicherungen der Box behalten ihre Kopie, bis sie ablaufen
[Aufbewahrung der Sicherungen].

## 3. Notwendigkeit und Verhältnismäßigkeit

**Rechtsgrundlage.** [Prüfen und festlegen, etwa Einwilligung nach Art. 6
Abs. 1 lit. a DSGVO, bei Beschäftigten § 26 BDSG, oder berechtigtes Interesse
nach Art. 6 Abs. 1 lit. f.] Unabhängig davon: Das nichtöffentlich gesprochene
Wort ohne Einwilligung aufzunehmen ist strafbar (§ 201 StGB). Alle
Teilnehmenden müssen vor der Aufnahme davon wissen und zustimmen.

**Mitbestimmung.** Eine Software, die Gespräche aufzeichnet, kann geeignet
sein, Verhalten oder Leistung zu überwachen (§ 87 Abs. 1 Nr. 6 BetrVG).
[Betriebsvereinbarung: ja / nein / nicht nötig, weil ...]

**Datenminimierung.**
- Aufnahme nur auf Knopfdruck, kein Dauermithören.
- Verarbeitung auf der eigenen Box; kein Cloud-Dienst für Spracherkennung
  oder Zusammenfassung.
- Löschen einer Besprechung entfernt Aufnahme, Transkript und Zusammenfassungen.
- Team-Mitglieder lesen nur, was geteilt ist; Aufnehmen, Benennen und Löschen
  bleiben beim Inhaber.

**Transparenz.** Die App erinnert bei jeder Aufnahme ("Erst allen Bescheid
sagen") und fragt auf jedem Telefon einmal nach. Die Seite `/privacy` der Box
beschreibt die Verarbeitung, mit Kontakt für Datenschutzfragen.
[Eigene Information an Beschäftigte und Gäste: ...]

**Betroffenenrechte.** Auskunft, Kopie, Berichtigung und Löschung (Art. 15
bis 17) bedient der Inhaber in der App; Löschen entfernt die Besprechung von
der Box. [Ablauf für Anfragen: ...]

## 4. Risiken

| Risiko | Folge für Betroffene | Eintritt | Schwere |
|---|---|---|---|
| Aufnahme ohne Wissen oder Einwilligung | Verletzung der Vertraulichkeit des Wortes, Strafbarkeit | [ ] | hoch |
| Unbefugter Zugriff auf Aufnahmen | vertrauliche Inhalte werden bekannt | [ ] | hoch |
| Nutzung zur Leistungs- oder Verhaltenskontrolle | Druck, Benachteiligung | [ ] | [ ] |
| Falsche Transkription oder Sprecherzuordnung | falsche Aussagen werden jemandem zugeschrieben | [ ] | mittel |
| Weitergabe durch Drive oder einen Cloud-Agenten | Inhalte verlassen das Unternehmen | [ ] | [ ] |
| Zu lange Speicherung | mehr Daten als nötig, länger als nötig | [ ] | mittel |

## 5. Maßnahmen

**Technisch (so arbeitet Minutes Pro):**
- Verschlüsselung auf dem Telefon vor dem Speichern und Senden; HTTPS auf dem
  Weg; auf der Box jede Datei mit AES-256-GCM, der Index mit SQLCipher; der
  Schlüssel liegt in den Einstellungen der App auf der Box.
- Ton während der Verarbeitung nur im Arbeitsspeicher (tmpfs).
- Anmeldung mit Passwort; Passwörter der Mitglieder als scrypt-Hash;
  Begrenzung falscher Versuche; Sitzungen laufen nach 30 Tagen ab.
- Rollen: der Inhaber nimmt auf und verwaltet; Mitglieder lesen nur, was er
  teilt; Einladungen gelten einmal und sieben Tage.
- Keine Stimmprofile, keine Wiedererkennung von Personen.
- Sprachmodelle laufen auf der Box; ein Modell, das die Box nur an einen
  externen Anbieter weiterreicht, bekommt keine Transkripte.

**Organisatorisch (füllt der Verantwortliche aus):**
- [Wer darf aufnehmen; Hinweis an alle Teilnehmenden vor jeder Aufnahme]
- [Löschfristen und wer sie umsetzt]
- [Ob Google Drive oder ein KI-Agent genutzt werden darf]
- [Zugang zur Box und zu ihren Sicherungen]
- [Umgang mit Besprechungen, in denen besondere Kategorien vorkommen]

## 6. Ergebnis

| | |
|---|---|
| Restrisiko nach den Maßnahmen | [niedrig / mittel / hoch, Begründung] |
| Stellungnahme der/des Datenschutzbeauftragten | [ ] |
| Konsultation der Aufsichtsbehörde nötig (Art. 36) | [ja / nein] |
| Freigabe | [Name, Funktion, Datum] |
| Nächste Überprüfung | [Datum, oder bei wesentlichen Änderungen] |
