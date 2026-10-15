"""Builds the deployable site into _site/.

Downloads EmulatorJS and the NES (FCEUmm), SNES (Snes9x), PC Engine (Beetle PCE)
and Game Boy (Gambatte) cores from npm,
unpacks the cores so no archive worker is needed at runtime, applies two small
patches to emulator.js, and writes the offline service worker.
"""
import hashlib, io, json, os, shutil, subprocess, sys, tarfile, tempfile, urllib.request

VERSION = "4.2.3"
ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "_site")
CORES = ["fceumm", "snes9x", "mednafen_pce", "gambatte"]
VARIANTS = ["wasm", "legacy-wasm"]


def npm_tarball(name):
    url = f"https://registry.npmjs.org/{name}/-/{name.split('/')[-1]}-{VERSION}.tgz"
    with urllib.request.urlopen(url) as r:
        return tarfile.open(fileobj=io.BytesIO(r.read()), mode="r:gz")


def make_icons(dest):
    from PIL import Image, ImageDraw
    os.makedirs(dest)
    S = 1024
    im = Image.new("RGB", (S, S), (21, 22, 27))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((232, 170, 792, 880), radius=48, fill=(228, 229, 234))
    for i in range(5):
        y = 210 + i * 34
        d.rounded_rectangle((300, y, 724, y + 16), radius=8, fill=(190, 192, 202))
    d.rounded_rectangle((292, 400, 732, 800), radius=28, fill=(91, 79, 168))
    d.rounded_rectangle((292, 400, 520, 800), radius=28, fill=(180, 38, 44))
    d.rectangle((480, 400, 512, 800), fill=(180, 38, 44))
    px, ox, oy = 60, 432, 450
    for c, r in [(0, 0), (0, 1), (1, 1), (0, 2), (1, 2), (2, 2), (0, 3), (1, 3), (0, 4)]:
        d.rectangle((ox + c * px, oy + r * px, ox + c * px + px - 4, oy + r * px + px - 4), fill=(244, 244, 247))
    d.rectangle((420, 850, 604, 880), fill=(21, 22, 27))
    for size, name in [(512, "icon-512.png"), (192, "icon-192.png"), (180, "apple-touch-icon.png")]:
        im.resize((size, size), Image.LANCZOS).save(os.path.join(dest, name))


def extract(t, dest):
    try:
        t.extractall(dest, filter="data")
    except TypeError:
        t.extractall(dest)


# --- Safari / iOS memory fix -------------------------------------------------------------------
# WebKit's optimizing WebAssembly compiler (OMG) uses gigabytes of memory on some of these cores'
# larger functions, so Safari reloads the page after a few minutes. WebKit never sends a function
# larger than 100,000 bytes to that compiler, so we pad every function over 10 KB with NOP
# instructions to just above that limit. They still run in WebKit's baseline compiler, at full
# speed for these consoles. The padding compresses away when GitHub Pages serves the file gzipped.
def _leb(b, i):
    r = s = 0
    while True:
        x = b[i]; i += 1; r |= (x & 0x7f) << s; s += 7
        if x < 0x80:
            return r, i


def _enc(n):
    out = bytearray()
    while True:
        x = n & 0x7f; n >>= 7
        if n:
            out.append(x | 0x80)
        else:
            out.append(x)
            return bytes(out)


def pad_wasm(path, threshold=10000, target=110000):
    b = open(path, "rb").read()
    assert b[:4] == b"\0asm"
    out = bytearray(b[:8]); i = 8; padded = 0
    while i < len(b):
        sid = b[i]; size, j = _leb(b, i + 1); body = b[j:j + size]; i = j + size
        if sid == 10:
            n, k = _leb(body, 0); entries = bytearray(_enc(n))
            for _ in range(n):
                fsz, k = _leb(body, k); fb = body[k:k + fsz]; k += fsz
                if threshold < fsz < target:
                    nl, p = _leb(fb, 0)
                    for _ in range(nl):
                        _, p = _leb(fb, p); p += 1
                    fb = fb[:p] + b"\x01" * (target - fsz) + fb[p:]
                    padded += 1
                entries += _enc(len(fb)) + fb
            body = bytes(entries)
        out.append(sid); out += _enc(len(body)) + body
    open(path, "wb").write(bytes(out))
    return padded


def patch_emulator(path):
    s = open(path, encoding="utf-8").read()
    rep = [
        ('            let filename = this.getCore() + (threads ? "-thread" : "") + legacy + "-wasm.data";\n',
         '            let filename = this.getCore() + (threads ? "-thread" : "") + legacy + "-wasm.data";\n            this.coreFilename = filename;\n'),
        ('            this.checkCompression(new Uint8Array(data), this.localization("Decompress Game Core")).then((data) => {',
         '            const unpacked = (data && data.__unpacked) ? Promise.resolve(data.files) : this.checkCompression(new Uint8Array(data), this.localization("Decompress Game Core"));\n            unpacked.then((data) => {'),
        ('            const corePath = "cores/" + filename;\n',
         '''            // Cartridge Shelf: prefer pre-extracted core files (no archive worker, works under strict CSP).
            try {
                const dir = "cores/x/" + filename.replace(/\\.data$/, "") + "/";
                const get = (name, type) => this.downloadFile(dir + name, name === "core.wasm" ? (progress) => {
                    this.textElem.innerText = this.localization("Download Game Core") + progress;
                } : null, false, { responseType: type, method: "GET" });
                const names = ["core.wasm", "core.js", "core.json", "build.json", "license.txt"];
                const parts = await Promise.all(names.map((n) => get(n, "arraybuffer")));
                if (parts[0] !== -1 && parts[1] !== -1 && parts[2] !== -1) {
                    const files = {};
                    parts.forEach((p, i) => { if (p !== -1 && p.data) files[names[i]] = new Uint8Array(p.data); });
                    gotCore({ __unpacked: true, files });
                    return;
                }
            } catch (e) {
                console.warn("Extracted core not available, falling back to archive", e);
            }
            const corePath = "cores/" + filename;
'''),
        ('''        script.addEventListener("load", () => {
            this.initModule(wasm, thread);
        });
        document.body.appendChild(script);''',
         '''        script.addEventListener("load", () => {
            this.initModule(wasm, thread);
        });
        // Cartridge Shelf: if blob: scripts are blocked (strict CSP), load the same core script from a served file.
        script.addEventListener("error", () => {
            const fallback = this.createElement("script");
            fallback.src = this.config.dataPath + "cores/x/" + String(this.coreFilename || "").replace(/\\.data$/, "") + "/core.js";
            fallback.addEventListener("load", () => this.initModule(wasm, thread));
            fallback.addEventListener("error", () => this.startGameError(this.localization("Error loading EmulatorJS runtime")));
            document.body.appendChild(fallback);
        });
        document.body.appendChild(script);'''),
        ('            totalDependencies: 0,\n            locateFile: function (fileName) {',
         '            totalDependencies: 0,\n            wasmBinary: wasmData,\n            locateFile: function (fileName) {'),
        # EJS_DEBUG_XX is only used to load the uncompressed scripts; keep logging/verbose mode off.
        ('        this.debug = (window.EJS_DEBUG_XX === true);\n',
         '        this.debug = (window.EJS_DEBUG_XX === true) && (window.EJS_VERBOSE === true);\n'),
        # Gamepads: look them up by their number, not by position in the list (Chrome may number a pad 1, 2, ...).
        ('                    this.gamepadSelection[i] = this.gamepad.gamepads[e.gamepadIndex].id + "_" + this.gamepad.gamepads[e.gamepadIndex].index;',
         '                    this.gamepadSelection[i] = (this.gamepad.gamepads.find((g) => g && g.index === e.gamepadIndex) || {}).id + "_" + e.gamepadIndex;'),
        ('        const gamepadIndex = this.gamepadSelection.indexOf(this.gamepad.gamepads[e.gamepadIndex].id + "_" + this.gamepad.gamepads[e.gamepadIndex].index);',
         '        const eventPad = this.gamepad.gamepads.find((g) => g && g.index === e.gamepadIndex);\n        if (!eventPad) return;\n        const gamepadIndex = this.gamepadSelection.indexOf(eventPad.id + "_" + eventPad.index);'),
        # Gamepads already active at start were never assigned to a player; announce them once listeners exist.
        ('        this.gamepad.on("buttonup", this.gamepadEvent.bind(this));\n    }',
         '        this.gamepad.on("buttonup", this.gamepadEvent.bind(this));\n        // Cartridge Shelf: controllers already active when the emulator starts were detected before the\n        // listeners above existed, so they were never assigned to a player. Announce them now.\n        this.gamepad.gamepads.forEach((g) => { if (g) this.gamepad.dispatchEvent("connected", { gamepadIndex: g.index }); });\n    }'),
        # Show the on-screen controller on touch screens even when the game starts automatically.
        ('        this.touch = false;\n',
         '        this.touch = (window.EJS_touchUI === true);\n'),
        # On-screen controller: a delayed hide during screen setup could remove it right after the game started.
        ('            if (this.touch) {\n                this.virtualGamepad.style.display = "";\n            }\n            this.handleResize();',
         '            if (this.touch) {\n                this.virtualGamepadWanted = true;\n                this.virtualGamepad.style.display = "";\n            }\n            this.handleResize();'),
        ('        this.toggleVirtualGamepad = (show) => {\n            this.virtualGamepad.style.display = show ? "" : "none";',
         '        this.toggleVirtualGamepad = (show) => {\n            this.virtualGamepadWanted = !!show;\n            this.virtualGamepad.style.display = show ? "" : "none";'),
        ('                setTimeout(() => {\n                    this.virtualGamepad.style.display = "none";\n                    this.virtualGamepad.style.opacity = "";\n                }, 250)',
         '                setTimeout(() => {\n                    // Cartridge Shelf: don\'t hide the controller if it was asked to show meanwhile.\n                    if (!this.virtualGamepadWanted) this.virtualGamepad.style.display = "none";\n                    this.virtualGamepad.style.opacity = "";\n                }, 250)'),
    ]
    for a, b in rep:
        if a not in s:
            sys.exit("patch target not found in emulator.js: " + a[:80])
        s = s.replace(a, b, 1)
    open(path, "w", encoding="utf-8").write(s)


def main():
    if os.path.exists(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)
    for f in ["index.html", "manifest.webmanifest"]:
        shutil.copy(os.path.join(ROOT, f), OUT)
    make_icons(os.path.join(OUT, "icons"))
    emu = os.path.join(OUT, "emu")
    tmp = tempfile.mkdtemp()

    t = npm_tarball("@emulatorjs/emulatorjs")
    extract(t, os.path.join(tmp, "ejs"))
    data = os.path.join(tmp, "ejs", "package", "data")
    os.makedirs(os.path.join(emu, "localization"))
    for f in ["loader.js", "emulator.css", "version.json"]:
        shutil.copy(os.path.join(data, f), emu)
    shutil.copytree(os.path.join(data, "src"), os.path.join(emu, "src"))
    shutil.copytree(os.path.join(data, "compression"), os.path.join(emu, "compression"), ignore=shutil.ignore_patterns("*.md"))
    for f in ["ja-JA.json", "en-US.json"]:
        shutil.copy(os.path.join(data, "localization", f), os.path.join(emu, "localization"))
    shutil.copy(os.path.join(tmp, "ejs", "package", "LICENSE"), os.path.join(emu, "LICENSE.txt"))
    patch_emulator(os.path.join(emu, "src", "emulator.js"))

    os.makedirs(os.path.join(emu, "cores", "reports"))
    for core in CORES:
        t = npm_tarball(f"@emulatorjs/core-{core}")
        d = os.path.join(tmp, core)
        extract(t, d)
        pkg = os.path.join(d, "package")
        shutil.copy(os.path.join(pkg, "reports", f"{core}.json"), os.path.join(emu, "cores", "reports"))
        for v in VARIANTS:
            name = f"{core}-{v}"
            x = os.path.join(tmp, "x", name)
            os.makedirs(x)
            subprocess.run(["7z", "x", "-y", f"-o{x}", os.path.join(pkg, f"{name}.data")], check=True, stdout=subprocess.DEVNULL)
            dst = os.path.join(emu, "cores", "x", name)
            os.makedirs(dst)
            for f in os.listdir(x):
                src = os.path.join(x, f)
                if f.endswith("_libretro.js"):
                    shutil.copy(src, os.path.join(dst, "core.js"))
                elif f.endswith("_libretro.wasm"):
                    shutil.copy(src, os.path.join(dst, "core.wasm"))
                    print(name, "padded", pad_wasm(os.path.join(dst, "core.wasm")), "functions")
                elif f in ("core.json", "build.json", "license.txt"):
                    shutil.copy(src, dst)

    # service worker: precache the app and the default (WebGL1) cores; cache the rest on first use
    files, h = ["./", "index.html", "manifest.webmanifest"], hashlib.sha1()
    for root, _, fs in os.walk(OUT):
        for f in sorted(fs):
            full = os.path.join(root, f)
            p = os.path.relpath(full, OUT).replace(os.sep, "/")
            h.update(open(full, "rb").read())
            if p in ("index.html", "manifest.webmanifest") or p.endswith("LICENSE.txt") or p.startswith("emu/compression/libunrar"):
                continue
            if "cores/x/" in p and "-legacy-" not in p:
                continue
            files.append(p)
    sw = open(os.path.join(ROOT, "sw.template.js"), encoding="utf-8").read()
    sw = sw.replace("__VERSION__", h.hexdigest()[:10]).replace("__FILES__", json.dumps(sorted(set(files)), indent=1))
    open(os.path.join(OUT, "sw.js"), "w", encoding="utf-8").write(sw)
    open(os.path.join(OUT, ".nojekyll"), "w").close()
    print("built", OUT, len(files), "precached files")


if __name__ == "__main__":
    main()
