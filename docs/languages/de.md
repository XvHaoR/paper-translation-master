# Fachartikel in Zotero lesen und übersetzen

[简体中文](../../README.md) · [English](en.md) · [日本語](ja.md) · [Español](es.md) · [Français](fr.md) · [Deutsch](de.md)

Paper Translation Master zeigt ein PDF neben dem erkannten Text oder der Übersetzung an. Eine falsch einsortierte Bildunterschrift lässt sich verschieben. Wenn Ihnen der Titel eines Artikels entfallen ist, können Sie Ihre Bibliothek anhand einer inhaltlichen Beschreibung durchsuchen und zur Fundstelle springen.

## Version 0.8.2 installieren

1. Laden Sie das vollständige Paket für Zotero 7–10 herunter. Das Mac-Paket setzt einen Mac mit Apple-Chip und macOS 14 oder neuer voraus. [Windows x64](https://github.com/XvHaoR/paper-translation-master/releases/download/v0.8.2/paper-translation-master-windows-x64-0.8.2.zip) · [macOS arm64](https://github.com/XvHaoR/paper-translation-master/releases/download/v0.8.2/paper-translation-master-macos-arm64-0.8.2.zip)
2. Beenden Sie Zotero. Entpacken Sie die ZIP-Datei in einen Ordner, den Sie dauerhaft behalten. Führen Sie unter Windows `Install.cmd` und unter macOS `Install.command` aus.
3. Öffnen Sie die Plugin-Verwaltung in Zotero. Wählen Sie im Zahnradmenü die Installation aus einer Datei, wählen Sie die XPI-Datei aus demselben Ordner und starten Sie Zotero neu. Aktualisieren Sie bei einem Upgrade auch die Laufzeitumgebung.

## Den ersten Artikel lesen

Für ein PDF mit markierbarem Text eignet sich **直接解析 PDF**. Bei Scans können Sie eine lokale OCR, die Cloud-Dienste von PaddleOCR oder MinerU oder einen eigenen MinerU-Server wählen. Bei Cloud-OCR wird das ausgewählte PDF an den Anbieter übertragen. Für die Übersetzung tragen Sie Modell und Endpunkt in den Einstellungen ein.

**逐段双语** zeigt Original und Übersetzung absatzweise an. Kommentare zur Übersetzung bleiben in der übersetzten Fassung. Das mitgelieferte mehrsprachige Suchmodell läuft auf der CPU. [Bebilderte Anleitung mit chinesischen Menübezeichnungen](../TUTORIAL.md)
