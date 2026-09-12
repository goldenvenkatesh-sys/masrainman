*{box-sizing:border-box}
html,body{margin:0;padding:0;font-family:Arial,sans-serif;background:#eef3f8;color:#17202a}
body{min-height:100vh}
header{padding:14px 18px;background:linear-gradient(135deg,#08145a,#234bbd);color:#fff;box-shadow:0 2px 8px rgba(0,0,0,.15)}
header h1{margin:0 0 4px;font-size:22px}
#time{font-size:13px;line-height:1.5}
main{display:grid;grid-template-columns:250px minmax(0,1fr);gap:12px;padding:12px;max-width:1500px;margin:auto}
aside{background:#fff;border-radius:10px;padding:15px;box-shadow:0 2px 8px rgba(0,0,0,.08);height:max-content}
aside h2{margin:0 0 10px;font-size:17px}
aside label{display:block;margin:10px 0;font-size:14px}
aside input.model{margin-right:7px}
aside select{display:block;width:100%;margin-top:5px;padding:8px;border:1px solid #ccd5df;border-radius:6px;background:#fff}
aside input[type=range]{width:100%;margin-top:6px}
aside hr{border:0;border-top:1px solid #e1e6ec;margin:14px 0}
.note{font-size:11px;line-height:1.45;color:#66717d}

/* Section layout holds map and places the colorbar strictly underneath */
section{
  min-width:0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

#map{height:760px;min-height:500px;position:relative;overflow:hidden;border-radius:10px;box-shadow:0 2px 10px rgba(0,0,0,.15);background:#dce7f2}
.rainfall-overlay{position:absolute!important;pointer-events:none!important}

/* Colorbar Placed Directly Below the Map */
#legend {
  position: relative;
  width: 100%;
  max-width: 100%;
  padding: 10px 14px;
  background: rgba(255,255,255,0.97);
  border: 1px solid rgba(0,0,0,0.12);
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.08);
  font-size: 10px;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.legend-title {
  font-weight: 700;
  font-size: 11px;
  text-align: center;
  margin-bottom: 4px;
}

.legend-source {
  display: none;
}

.legend-bar {
  display: flex;
  flex-direction: row;
  justify-content: center;
  gap: 1px;
  margin-top: 4px;
  width: 100%;
  overflow-x: auto;
}

.legend-row {
  display: flex;
  flex-direction: column-reverse;
  align-items: center;
  gap: 3px;
  flex: 1;
  min-width: 22px;
}

.legend-row i {
  display: block;
  width: 100%;
  height: 16px;
  border: none;
  border-radius: 0;
}

.legend-row span {
  font-size: 9px;
  line-height: 1;
  color: #17202a;
}

@media(max-width:800px){
  main{grid-template-columns:1fr}
  #map{height:650px;min-height:420px}
  .legend-row{min-width:14px}
  .legend-row span{font-size:7px;transform:scale(0.85)}
}

.wind-toggle{font-weight:700}
.wind-note{font-size:10px;line-height:1.4;color:#66717d;margin-top:5px}
#windSourceWrap select{margin-top:5px}

.rain-tooltip {
  position: absolute;
  z-index: 1000;
  min-width: 185px;
  max-width: 245px;
  padding: 9px 11px;
  border-radius: 8px;
  background: rgba(255,255,255,.96);
  color: #17212b;
  box-shadow: 0 3px 14px rgba(0,0,0,.22);
  border: 1px solid rgba(0,0,0,.12);
  font: 12px/1.35 Arial,sans-serif;
  pointer-events: none;
}
.rain-tooltip-title {
  font-weight: 700;
  margin-bottom: 5px;
}
.rain-tooltip-main {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  padding-bottom: 5px;
  margin-bottom: 4px;
  border-bottom: 1px solid #ddd;
}
.rain-tooltip-main b {
  font-size: 17px;
}
.rain-tooltip-main span,
.rain-tooltip small {
  color: #66717d;
}
.rain-tooltip > div:not(.rain-tooltip-title):not(.rain-tooltip-main) {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}
.rain-tooltip small {
  display: block;
  margin-top: 5px;
}

.run-details{margin:10px 0 12px;padding:9px 10px;border:1px solid #dbe3ec;border-radius:8px;background:#f7f9fc}
.run-details-title{font-weight:700;font-size:13px;margin-bottom:7px;color:#17202a}
.run-main{display:flex;justify-content:space-between;gap:8px;font-size:11px;line-height:1.5}
.run-main span{color:#66717d}
.run-main b{font-size:11px}
.run-valid{font-size:11px;line-height:1.45;margin-top:2px;color:#234bbd;font-weight:700}
.run-subtitle{font-size:10px;text-transform:uppercase;letter-spacing:.4px;color:#66717d;margin:7px 0 3px}
.run-model-row{display:flex;justify-content:space-between;gap:6px;font-size:10px;line-height:1.55}
.run-model-row b{font-weight:700}

.aifs-heading{font-weight:700;font-size:13px;margin:4px 0 7px;color:#17202a}
#aifsModelWrap{font-size:12px}
#aifsModelWrap select{font-size:12px}

.leaflet-container {
  background: #d8edf7;
}

.leaflet-tile {
  filter: none;
}

.leaflet-control-zoom {
  box-shadow: 0 1px 4px rgba(0,0,0,.22) !important;
}
