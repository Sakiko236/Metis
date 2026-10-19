"""
Generate desktop and application icons using the Blue Kawaii Cloud Cat Mascot.
This script renders the mascot SVG to high-resolution PNG using Chrome headless,
then generates all required icon formats in src-tauri/icons/ and assets/.
"""

import os
import sys
import subprocess
import tempfile
from PIL import Image

def get_mascot_svg(mood="happy", width=512, height=512, corner_radius=128):
    # Blue 'metis' palette colors
    body_grad_start = "#7dd3fc"
    body_grad_mid = "#0284c7"
    body_grad_end = "#0369a1"
    belly_fill = "#f0f9ff"
    eyes_color = "#0c4a6e"
    glow_color = "rgba(56, 189, 248, 0.45)"
    blush_color = "#fb7185"
    sparkle_color = "#38bdf8"

    # Eyes & Mouth based on mood
    if mood == "happy":
        eyes_html = f"""
          <path d="M 205,258 Q 220,238 235,258" fill="none" stroke="{eyes_color}" stroke-width="6" stroke-linecap="round"/>
          <path d="M 277,258 Q 292,238 307,258" fill="none" stroke="{eyes_color}" stroke-width="6" stroke-linecap="round"/>
        """
        mouth_html = f"""<path d="M 242,284 Q 256,304 270,284" fill="{eyes_color}" stroke="{eyes_color}" stroke-width="3" stroke-linecap="round"/>"""
    elif mood == "wink":
        eyes_html = f"""
          <circle cx="220" cy="252" r="10" fill="{eyes_color}"/>
          <circle cx="217" cy="249" r="3.5" fill="#ffffff"/>
          <path d="M 277,255 Q 292,238 307,255" fill="none" stroke="{eyes_color}" stroke-width="6" stroke-linecap="round"/>
        """
        mouth_html = f"""<path d="M 244,285 Q 256,296 268,285" fill="none" stroke="{eyes_color}" stroke-width="4.5" stroke-linecap="round"/>"""
    elif mood == "love":
        eyes_html = """
          <path d="M 220,238 L 223,248 L 233,250 L 225,257 L 227,267 L 220,261 L 213,267 L 215,257 L 207,250 L 217,248 Z" fill="#f43f5e"/>
          <path d="M 292,238 L 295,248 L 305,250 L 297,257 L 299,267 L 292,261 L 285,267 L 287,257 L 279,250 L 289,248 Z" fill="#f43f5e"/>
        """
        mouth_html = """<path d="M 240,282 Q 256,306 272,282 Z" fill="#f43f5e"/>"""
    elif mood == "focused":
        eyes_html = f"""
          <path d="M 206,244 L 226,254 L 206,264" fill="none" stroke="{eyes_color}" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>
          <path d="M 306,244 L 286,254 L 306,264" fill="none" stroke="{eyes_color}" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>
        """
        mouth_html = f"""<path d="M 246,288 Q 256,280 266,288" fill="none" stroke="{eyes_color}" stroke-width="4.5" stroke-linecap="round"/>"""
    elif mood == "sleepy":
        eyes_html = f"""
          <path d="M 205,248 Q 220,266 235,248" fill="none" stroke="{eyes_color}" stroke-width="6" stroke-linecap="round"/>
          <path d="M 277,248 Q 292,266 307,248" fill="none" stroke="{eyes_color}" stroke-width="6" stroke-linecap="round"/>
          <circle cx="325" cy="230" r="6" fill="none" stroke="#38bdf8" stroke-width="2.5" opacity="0.8"/>
          <circle cx="338" cy="216" r="9" fill="none" stroke="#38bdf8" stroke-width="2.5" opacity="0.8"/>
        """
        mouth_html = f"""<circle cx="256" cy="286" r="3.5" fill="{eyes_color}"/>"""
    else:  # shocked
        eyes_html = f"""
          <circle cx="220" cy="252" r="14" fill="{eyes_color}"/>
          <circle cx="216" cy="248" r="5" fill="#ffffff"/>
          <circle cx="292" cy="252" r="14" fill="{eyes_color}"/>
          <circle cx="288" cy="248" r="5" fill="#ffffff"/>
        """
        mouth_html = f"""<ellipse cx="256" cy="292" rx="7" ry="11" fill="{eyes_color}"/>"""

    blush_html = f"""
      <ellipse cx="196" cy="272" rx="14" ry="8" fill="{blush_color}" opacity="0.75" />
      <ellipse cx="316" cy="272" rx="14" ry="8" fill="{blush_color}" opacity="0.75" />
    """

    r = corner_radius
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="{width}" height="{height}">
  <defs>
    <linearGradient id="mainBodyGrad" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="{body_grad_start}"/>
      <stop offset="50%" stop-color="{body_grad_mid}"/>
      <stop offset="100%" stop-color="{body_grad_end}"/>
    </linearGradient>

    <radialGradient id="iconBgGrad" cx="50%" cy="35%" r="80%">
      <stop offset="0%" stop-color="#111827"/>
      <stop offset="60%" stop-color="#080c16"/>
      <stop offset="100%" stop-color="#03050a"/>
    </radialGradient>

    <filter id="shadowFilter" x="-20%" y="-20%" width="140%" height="140%">
      <feDropShadow dx="0" dy="16" stdDeviation="18" flood-color="{glow_color}"/>
      <feDropShadow dx="0" dy="8" stdDeviation="6" flood-color="rgba(0,0,0,0.35)"/>
    </filter>
  </defs>

  <!-- Dark squircle frame -->
  <rect width="512" height="512" rx="{r}" fill="url(#iconBgGrad)" stroke="#1e293b" stroke-width="2.5"/>
  <rect x="3" y="3" width="506" height="506" rx="{max(0, r - 3)}" fill="none" stroke="#38bdf8" stroke-width="1.5" opacity="0.25"/>

  <!-- Sparkles -->
  <g opacity="0.9">
    <path d="M 88,140 Q 98,140 98,130 Q 98,140 108,140 Q 98,140 98,150 Q 98,140 88,140 Z" fill="{sparkle_color}" />
    <path d="M 405,115 Q 417,115 417,103 Q 417,115 429,115 Q 417,115 417,127 Q 417,115 405,115 Z" fill="{sparkle_color}" />
    <circle cx="430" cy="155" r="4" fill="#ffffff" opacity="0.8"/>
    <circle cx="80" cy="180" r="3" fill="#ffffff" opacity="0.8"/>
  </g>

  <!-- Mascot Body -->
  <g id="mascotWrapper">
    <!-- Cat-ear cloud M -->
    <path d="
      M 125,360 
      C 105,320 100,210 135,160 
      L 175,115 
      C 195,145 220,200 256,225 
      C 292,200 317,145 337,115 
      L 377,160 
      C 412,210 407,320 387,360 
      C 355,400 305,370 256,370 
      C 207,370 157,400 125,360 Z
    " fill="url(#mainBodyGrad)" filter="url(#shadowFilter)"/>

    <!-- Pink inner cat ears -->
    <polygon points="148,168 174,136 182,174" fill="{blush_color}" opacity="0.65"/>
    <polygon points="364,168 338,136 330,174" fill="{blush_color}" opacity="0.65"/>

    <!-- Fluffy cloud belly -->
    <path d="M 190,340 C 200,300 220,285 256,285 C 292,285 312,300 322,340 C 285,360 227,360 190,340 Z" fill="{belly_fill}" opacity="0.95"/>

    <!-- Cute cat paw prints -->
    <ellipse cx="170" cy="365" rx="14" ry="10" fill="#ffffff" opacity="0.9"/>
    <ellipse cx="342" cy="365" rx="14" ry="10" fill="#ffffff" opacity="0.9"/>

    <!-- Face elements -->
    <g id="faceGroup">
      {eyes_html}
      {mouth_html}
      {blush_html}
    </g>
  </g>
</svg>"""
    return svg

def render_svg_to_png(svg_str, output_path, size=512):
    chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    if not os.path.exists(chrome_path):
        chrome_path = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    if not os.path.exists(chrome_path):
        raise FileNotFoundError(f"Neither Chrome nor Edge found for headless rendering.")

    html_content = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  html, body {{ width: {size}px; height: {size}px; overflow: hidden; background: transparent; }}
  svg {{ display: block; width: {size}px; height: {size}px; }}
</style>
</head>
<body>
{svg_str}
</body>
</html>"""

    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as tf:
        tf.write(html_content)
        temp_html = tf.name

    try:
        cmd = [
            chrome_path,
            "--headless=new",
            "--hide-scrollbars",
            "--default-background-color=00000000",
            f"--window-size={size},{size}",
            f"--screenshot={output_path}",
            temp_html
        ]
        subprocess.run(cmd, check=True, capture_output=True)
    finally:
        if os.path.exists(temp_html):
            os.remove(temp_html)

def main():
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    icons_dir = os.path.join(repo_root, "src-tauri", "icons")
    assets_dir = os.path.join(repo_root, "assets")

    print("[*] Generating Mascot SVG...")
    svg_code = get_mascot_svg(mood="happy", width=512, height=512, corner_radius=128)

    temp_png = os.path.join(tempfile.gettempdir(), "metis_mascot_512.png")
    print(f"[*] Rendering SVG to {temp_png} via Chrome...")
    render_svg_to_png(svg_code, temp_png, size=512)

    base_img = Image.open(temp_png).convert("RGBA")

    # Target files and sizes
    targets = {
        os.path.join(assets_dir, "metis-icon.png"): (512, 512),
        os.path.join(icons_dir, "icon.png"): (512, 512),
        os.path.join(icons_dir, "128x128.png"): (128, 128),
        os.path.join(icons_dir, "128x128@2x.png"): (256, 256),
        os.path.join(icons_dir, "32x32.png"): (32, 32),
        os.path.join(icons_dir, "Square30x30Logo.png"): (30, 30),
        os.path.join(icons_dir, "Square44x44Logo.png"): (44, 44),
        os.path.join(icons_dir, "Square71x71Logo.png"): (71, 71),
        os.path.join(icons_dir, "Square89x89Logo.png"): (89, 89),
        os.path.join(icons_dir, "Square107x107Logo.png"): (107, 107),
        os.path.join(icons_dir, "Square142x142Logo.png"): (142, 142),
        os.path.join(icons_dir, "Square150x150Logo.png"): (150, 150),
        os.path.join(icons_dir, "Square284x284Logo.png"): (284, 284),
        os.path.join(icons_dir, "Square310x310Logo.png"): (310, 310),
        os.path.join(icons_dir, "StoreLogo.png"): (50, 50),
    }

    for path, (w, h) in targets.items():
        print(f"[*] Resizing to {w}x{h} -> {os.path.relpath(path, repo_root)}")
        resized = base_img.resize((w, h), Image.Resampling.LANCZOS)
        resized.save(path, "PNG")

    # Generate multi-res icon.ico
    ico_path = os.path.join(icons_dir, "icon.ico")
    print(f"[*] Generating multi-resolution ICO -> {os.path.relpath(ico_path, repo_root)}")
    ico_sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    base_img.save(ico_path, format="ICO", sizes=ico_sizes)

    if os.path.exists(temp_png):
        os.remove(temp_png)

    print("[+] All mascot icons successfully updated!")

if __name__ == "__main__":
    main()
