// Minimal Leaflet stand-in: enough for the board's code paths, and it records
// every setStyle so we can see whether a highlight actually reaches a marker.
module.exports = function attach(w) {
  const calls = { setStyle: [], setRadius: 0, bringToFront: 0, panTo: 0, fitBounds: 0, markers: [] };
  function Marker(latlng, opts) {
    this._ll = latlng; this.options = Object.assign({}, opts);
    // Faithful to Leaflet: a CircleMarker's drawn radius is held separately
    // and only setRadius touches it. setStyle({radius}) changes the option and
    // nothing you can see — which is exactly the bug this stub must expose.
    this.drawnRadius = opts.radius;
    this.setStyle = (s) => { Object.assign(this.options, s); calls.setStyle.push(s); return this; };
    this.setRadius = (r) => { this.drawnRadius = r; calls.setRadius++; return this; };
    this.bringToFront = () => { calls.bringToFront++; return this; };
    this.getLatLng = () => ({ lat: latlng[0], lng: latlng[1] });
    this.bindTooltip = () => this;
    this.on = () => this;
    this.addTo = (layer) => { layer._layers.push(this); calls.markers.push(this); return this; };
  }
  const L = {
    map: () => ({
      _layers: [],
      setView() { return this; },
      fitBounds() { calls.fitBounds++; return this; },
      panTo() { calls.panTo++; return this; },
      getBounds: () => ({ contains: () => true }),
      addLayer() { return this; }, removeLayer() { return this; },
    }),
    tileLayer: () => ({ addTo: () => ({}) }),
    layerGroup: () => { const g = { _layers: [], clearLayers() { g._layers = []; return g; },
                                    addTo: () => g }; return g; },
    circleMarker: (ll, o) => new Marker(ll, o),
    latLngBounds: (pts) => ({ pad: () => pts }),
  };
  w.L = L;
  return calls;
};
