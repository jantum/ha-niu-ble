# NIU KQi (Bluetooth) für Home Assistant

Inoffizielle Integration für NIU-KQi-E-Scooter ohne Mobilfunkmodul. Home Assistant
verbindet sich per Bluetooth LE kurz mit dem Roller, liest die Werte und trennt wieder.

**Nur lesend.** Es sind keine Steuerbefehle (Sperren, Einstellungen usw.) enthalten.

Getestet mit einem KQi 300P (BLE-Version 10, Service-UUID `…daea50`). Modelle mit
BLE-Version 20/21 werden nicht unterstützt.

## Entitäten

| Entität | Hinweis |
|---|---|
| Akku (%) | |
| Reichweite (km) | Skalierung unbestätigt; 0 wird als „unbekannt“ gezeigt |
| Fahrmodus | Rohwert |
| Höchstgeschwindigkeit (km/h) | |
| Fehlercode | Klartext im Attribut `description` |
| Akkukapazität (mAh) | Einheit unbestätigt |
| Letzter Abruf | |
| In Reichweite | aus den Bluetooth-Advertisements |
| Ladegerät / Lädt | unterwegs „unbekannt“ |
| Fahrten nicht synchronisiert | Hinweis, die NIU-App zu öffnen |
| Standort (`device_tracker`) | in Reichweite = zuhause, sonst Position eines Handy-Trackers |

Standardmäßig deaktiviert: Ladezähler, Firmware-Stände.

Ist der Roller unterwegs, bleiben die zuletzt gelesenen Werte stehen (auch über einen
HA-Neustart hinweg).

## Installation (HACS)

1. HACS → Menü → Benutzerdefinierte Repositories
2. URL dieses Repos eintragen, Typ „Integration“
3. „NIU KQi (Bluetooth)“ herunterladen, Home Assistant neu starten

## Einrichtung

Benötigt werden die Bluetooth-MAC des Rollers sowie `blePassword` und `bleAes`. Beide
Schlüssel liefert die NIU-Cloud; am einfachsten mit
[niu-kqi](https://github.com/BaesTheorem/niu-kqi):

```
python kqi_ble.py login <NIU-Konto>
python kqi_ble.py setup --mac <MAC>
```

Danach stehen sie in `secrets/scooter.json` unter `ble` (`password`, `aes`).

Einstellungen → Geräte & Dienste → Integration hinzufügen → „NIU KQi (Bluetooth)“.
Ist der Roller in Reichweite, werden die Schlüssel beim Einrichten direkt geprüft.
Die NIU-App sollte dabei geschlossen sein.

Optionen: Handy-Tracker für den Standort unterwegs, Abrufintervall (Standard 5 min).

## Grenzen

- Kein Kilometerstand und kein Ladestrom per Bluetooth.
- Während HA verbunden ist (wenige Sekunden je Abruf), kann sich die NIU-App nicht verbinden, und umgekehrt.
- Der Standort unterwegs ist die Position des Handys, nicht des Rollers.

## Herkunft

Das Protokoll stammt aus [niu-kqi](https://github.com/BaesTheorem/niu-kqi) (MIT,
siehe `custom_components/niu_ble/LICENSE-niu-kqi.txt`) und wurde für Home Assistant neu
geschrieben. Kein Produkt von NIU, Nutzung auf eigenes Risiko.
