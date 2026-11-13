# Was dein Bot sehen kann

Dein Bot schaut sich das Spiel 60-mal pro Sekunde an.

## So gewinnst du

Meistens hat jeder Spieler nur ein Leben: Einmal rausgeflogen und du bist raus!
Stosse die anderen von der Bühne und bleibe selbst darauf. Wer als Letzter
noch dabei ist, gewinnt.

## Alle Spieler im Kampf

Dein Bot kann **alle** Spieler sehen, auch alle Gegner. Für jeden sieht er:

- `position.x` und `position.y`: wo sie sind
- `percent`: wie viel Schaden sie haben
- `stock`: wie viele Leben sie noch haben
- `character`: welche Figur sie spielen
- `action`: was sie gerade tun, zum Beispiel rennen oder angreifen
- `action_frame`: wie weit ihre aktuelle Bewegung schon ist
- `facing`: in welche Richtung sie schauen
- `on_ground` und `off_stage`: stehen sie auf dem Boden oder sind sie ausserhalb der Bühne?
- `jumps_left`: wie viele Sprünge sie noch haben
- `shield_strength`: wie stark ihr Schild ist
- `invulnerable`: ob sie gerade vor Treffern geschützt sind
- `hitstun_frames_left`: wie lange sie nach einem Treffer noch nicht handeln können
- `controller_state`: welche Tasten sie drücken

## Der Rest des Spiels

- `gamestate.players`: alle Spieler, nach Controller-Anschluss geordnet
- `gamestate.stage`: auf welcher Bühne du bist
- `gamestate.frame`: wie viele Spielbilder vergangen sind
- `gamestate.projectiles`: Dinge, die herumfliegen, zum Beispiel Laser und Feuerbälle

## Was dein Bot tun kann

Bei jedem Spielbild kann er die Steuerknüppel bewegen und Tasten drücken:

- Haupt-Stick: gehen, rennen, ducken, springen, zielen
- C-Stick: schnelle Smash-Angriffe
- A: angreifen
- B: Spezialattacken
- X oder Y: springen
- L oder R: Schild benutzen und ausweichen
- Z: greifen

## Was dein Bot nicht tun kann

- Den Code oder die Pläne eines anderen Teams sehen
- Die Figur eines anderen Spielers steuern
- Eine andere Figur wählen: Das entscheidet die Spielleitung
- Das Internet benutzen
- Die Bühne oder die Regeln ändern

## Gute Ideen zum Ausprobieren

- Schau dir die Bühne dieser Runde an: Wo sind die Kanten und Plattformen?
- Wer ist am nächsten? Wer hat den meisten Schaden?
- Was soll mein Bot tun, wenn er von der Kante fällt?
- Soll er angreifen, den Schild benutzen oder weglaufen, wenn jemand in der Nähe ist?
- Was kann deine Figur besonders gut?
