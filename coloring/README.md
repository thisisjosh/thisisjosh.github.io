Coloring App

A simple, dependency-free mobile friendly browser coloring book with bucket fill.

Quick Start

1. Start a local web server (Python):

```powershell
python -m http.server 8000
```

2. Open http://localhost:8000/ in your browser.

How to Use

- Click the **Library** button (📚) to select an SVG image.
- Use the **color picker** to choose a fill color.
- Click inside a region to fill it with the selected color.
- **Load** (📂) restores your last saved work.
- **Clear** (🗑️) resets the current image.

Features

- Flood-fill bucket tool with anti-aliased edge handling.
- Auto-save to browser IndexDB.
- No external dependencies—pure HTML, CSS, and JavaScript.

Adding Images

Place SVG files in the `images/` folder.
Run
```powershell
python update_images.py
```

Future Ideas

See `todo.md` for planned improvements and enhancements.
