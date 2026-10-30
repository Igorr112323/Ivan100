/* Карта массива: реальный снимок Sentinel-2 как подложка + контуры чеков,
   раскрашенные по решению оптимизатора. Цвет = состояние чека, а не украшение. */

import { useEffect, useMemo, useRef } from "react";
import L from "leaflet";
import { stateColor } from "./Marks";
import { num } from "../lib/api";

type Props = {
  imagery: { image: string; bounds: [[number, number], [number, number]]; date: string; scene: string; attribution: string } | null;
  checksGeo: any;
  cuts: Map<number, number>;
  selected: number | null;
  onSelect: (id: number | null) => void;
};

export default function MapView({ imagery, checksGeo, cuts, selected, onSelect }: Props) {
  const holder = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.GeoJSON | null>(null);
  const overlayRef = useRef<L.ImageOverlay | null>(null);

  useEffect(() => {
    if (!holder.current || mapRef.current) return;
    const map = L.map(holder.current, {
      zoomControl: true,
      attributionControl: true,
      scrollWheelZoom: true,
      zoomSnap: 0.25,
      minZoom: 11,
      maxZoom: 17,
    });
    map.setView([45.35, 38.2], 13);
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !imagery) return;
    if (overlayRef.current) {
      map.removeLayer(overlayRef.current);
    }
    const url = `/data/sentinel/${imagery.image}`;
    const bounds = L.latLngBounds(imagery.bounds[0], imagery.bounds[1]);
    const overlay = L.imageOverlay(url, bounds, { opacity: 1, interactive: false, className: "sat" });
    overlay.addTo(map);
    overlayRef.current = overlay;
    map.fitBounds(bounds);
  }, [imagery]);

  const styleFor = (feature: any) => {
    const id = feature?.properties?.id;
    const cut = cuts.has(id) ? cuts.get(id)! : 0;
    const isSel = selected === id;
    return {
      color: isSel ? "#b4341f" : "rgba(27,31,36,0.55)",
      weight: isSel ? 2 : 0.8,
      fillColor: stateColor(cut),
      fillOpacity: isSel ? 0.82 : 0.62,
    };
  };

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !checksGeo) return;
    if (layerRef.current) {
      map.removeLayer(layerRef.current);
    }
    const layer = L.geoJSON(checksGeo, {
      style: styleFor,
      onEachFeature: (feature, layer) => {
        const p = feature.properties;
        layer.bindTooltip(
          `Чек ${p.id} · ${num(p.area_ha, 1)} га · отм. ${num(p.elev_m, 1)} м`,
          { sticky: true, direction: "top", className: "mono" }
        );
        layer.on({
          click: () => onSelect(p.id as number),
        });
      },
    });
    layer.addTo(map);
    layerRef.current = layer;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [checksGeo]);

  useEffect(() => {
    const layer = layerRef.current;
    if (!layer) return;
    layer.setStyle((feature: any) => styleFor(feature));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, cuts]);

  const attribution = useMemo(
    () => imagery?.attribution ?? "Содержит обработанные данные Copernicus Sentinel",
    [imagery]
  );

  return (
    <div>
      <div className="map" ref={holder} />
      <div style={{ position: "relative" }}>
        <div className="map-caption mono" style={{ position: "absolute", bottom: 0, left: 0 }}>
          SENTINEL-2 · {imagery ? imagery.date : "—"} · {imagery ? imagery.scene : ""}
        </div>
      </div>
      <div className="src" style={{ marginTop: 6 }}>
        {attribution}. Контуры чеков — автоматическая сегментация водного зеркала по снимку.
      </div>
    </div>
  );
}
