# Cartridge Shelf

NES（ファミコン）と SNES（スーパーファミコン）のゲームを、Mac と iPhone のブラウザで遊べる Web アプリです。

**遊ぶ:** https://kazjaz.github.io/cartridge-shelf/

- 「ROMを追加」で .nes .sfc .smc .zip を読み込むと、棚に並びます
- iPhone は Safari で開いて「ホーム画面に追加」するとアプリとして起動し、オフラインでも動きます
- ROM とセーブは各端末のブラウザ内にだけ保存されます（このリポジトリには入りません）

## 仕組み

main に push すると GitHub Actions が prepare_site.py を実行し、npm から EmulatorJS と FCEUmm / Snes9x コアを取得して GitHub Pages に公開します。

## ライセンス

EmulatorJS（GPL-3.0）、FCEUmm（GPL-2.0）、Snes9x（非商用ライセンス）を使用しています。ROM は含まれていません。
