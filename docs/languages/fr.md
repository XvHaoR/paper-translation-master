# Lire et traduire des articles dans Zotero

[简体中文](../../README.md) · [English](en.md) · [日本語](ja.md) · [Español](es.md) · [Français](fr.md) · [Deutsch](de.md)

Paper Translation Master affiche le PDF à côté du texte reconnu ou de sa traduction. Vous pouvez déplacer une légende, annoter un passage et retrouver un article à partir d’une idée dont vous vous souvenez. Zotero conserve les résultats en pièces jointes.

## Installer la version 0.8.2

1. Téléchargez l’archive complète pour Zotero 7 à 10. Sur Mac, il faut une puce Apple et macOS 14 ou une version ultérieure. [Windows x64](https://github.com/XvHaoR/paper-translation-master/releases/download/v0.8.2/paper-translation-master-windows-x64-0.8.2.zip) · [macOS arm64](https://github.com/XvHaoR/paper-translation-master/releases/download/v0.8.2/paper-translation-master-macos-arm64-0.8.2.zip)
2. Quittez Zotero et décompressez l’archive dans un dossier que vous conserverez. Lancez `Install.cmd` sous Windows ou `Install.command` sous macOS.
3. Ouvrez le gestionnaire d’extensions dans le menu **Outils** de Zotero. Depuis la roue dentée, choisissez l’installation depuis un fichier, sélectionnez le XPI du même dossier et redémarrez Zotero. Pour une mise à jour, installez aussi le nouvel environnement fourni dans l’archive.

## Lire un premier document

Pour un PDF dont le texte est sélectionnable, choisissez **直接解析 PDF**. Pour un document numérisé, vous pouvez utiliser un moteur OCR local, PaddleOCR ou MinerU dans le cloud, ou votre propre serveur MinerU. Le mode cloud transmet le PDF choisi au prestataire. La traduction utilise le modèle et l’adresse configurés dans les réglages.

**逐段双语** associe chaque paragraphe à sa traduction. Les annotations du texte traduit restent dans la traduction. La recherche multilingue fonctionne en local avec le modèle inclus. [Tutoriel illustré avec les libellés chinois](../TUTORIAL.md)
