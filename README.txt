MASRAINMAN ZERO-COST RAINFALL WEB PACKAGE

Files:
  index.html  - public web page
  style.css   - page styling
  app.js      - Leaflet map and controls
  update.py   - data updater entry point
  update.yml  - GitHub Actions schedule
  app.py      - current Streamlit development app

IMPORTANT:
update.py is intentionally a safe placeholder. It must be replaced by the
production model-data updater before the scheduled GitHub workflow is enabled.
Do not publish an empty/placeholder product as a live forecast.

Planned flow:
GitHub Actions -> model processing -> data.json -> GitHub Pages -> Blogger iframe.

The rainfall colours in app.js match the supplied MasRainman default scale.
