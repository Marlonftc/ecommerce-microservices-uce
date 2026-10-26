"""Generate the separate k6 dashboard; never changes Stage A files or starts tests."""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = {"type": "prometheus", "uid": "uce-prometheus"}
GROUP = "testid,load_scenario,product_replicas,measurement_kind,comparison_key,phase"
BASE = ('testid=~"${testid:regex}",load_scenario=~"${load_scenario:regex}",'
        'product_replicas=~"${product_replicas:regex}",measurement_kind="${measurement_kind}"')


def expressions(base=BASE, phase='${phase:regex}'):
    selected = base + f',phase=~"{phase}",name=~"${{endpoint:regex}}"'
    live = f'max by(testid) (k6_vus{{{base}}} and ((time() - timestamp(k6_vus{{{base}}})) <= 15))'
    total = f'sum by({GROUP}) (rate(k6_http_reqs_total{{{selected}}}[30s]))'
    failed = f'sum by({GROUP}) (rate(k6_http_reqs_total{{{selected},expected_response="false"}}[30s]))'
    incomplete = f'sum by({GROUP}) (count_over_time(k6_http_reqs_total{{{selected}}}[30s]) < 2)'
    p95 = ('1000 * histogram_quantile(0.95, '
           f'sum by({GROUP}) (rate(k6_http_req_duration_seconds{{{selected}}}[30s])))')
    # A zero numerator is justified only by an observed, positive denominator.
    errors = f'100 * (({failed} or on({GROUP}) (0 * {total})) / ({total} > 0))'
    live_suffix = f' and on(testid) ({live})'
    suffix = live_suffix + f' unless on({GROUP}) ({incomplete})'
    return {"vus": f'max by(testid,load_scenario,product_replicas,measurement_kind) (k6_vus{{{base}}})' + live_suffix,
            "rps": f'({total})' + suffix, "p95": f'({p95})' + suffix, "errors": f'({errors})' + suffix,
            "last": f'max by(testid) (max_over_time(timestamp(k6_vus{{{base}}})[$__range:5s]))',
            "info": f'max by(testid,load_scenario,product_replicas,measurement_kind,comparison_key) '
                    f'(last_over_time(k6_vus{{{base}}}[$__range]))'}


def variable(name, label, query, multi=False, all_values=False):
    result = {"name": name, "label": label, "type": "query", "datasource": SOURCE,
              "query": {"query": query, "refId": name}, "refresh": 2, "sort": 2,
              "multi": multi, "includeAll": all_values, "options": [],
              "current": {"text": "Sin selección", "value": "__none__"}}
    if all_values:
        result.update(allValue=".+", current={"text": "All", "value": "$__all"})
    return result


def custom(name, label, values, current, hidden=0):
    return {"name": name, "label": label, "type": "custom", "query": ",".join(values), "hide": hidden,
            "multi": False, "includeAll": False, "current": {"text": current, "value": current},
            "options": [{"text": v, "value": v, "selected": v == current} for v in values]}


def comparison_input(name):
    # URL values come from the local JSON validator, never from automatic selection of another run.
    return {"name": name, "label": name, "type": "textbox", "hide": 2,
            "query": "__none__", "current": {"text": "__none__", "value": "__none__"}, "options": []}


def target(expression, ref="A", instant=False, legend="{{testid}} · {{phase}} · {{product_replicas}} rép."):
    return {"refId": ref, "expr": expression, "datasource": SOURCE, "editorMode": "code",
            "instant": instant, "range": not instant, "legendFormat": legend, "interval": "5s"}


def panel(identity, title, unit, queries, x, y, width=12, height=8, kind="timeseries", description=""):
    defaults = {"unit": unit, "noValue": "Sin datos", "color": {"mode": "palette-classic"},
                "mappings": [{"type": "special", "options": {"match": match, "result": {"text": "Sin datos", "color": "#53657a"}}}
                             for match in ("null", "nan")]}
    options = {"legend": {"displayMode": "table", "placement": "bottom", "calcs": []},
               "tooltip": {"mode": "multi", "sort": "none"}}
    if kind == "timeseries":
        defaults["custom"] = {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 4,
                              "spanNulls": False, "insertNulls": False, "showPoints": "auto",
                              "axisCenteredZero": False, "axisLabel": "", "stacking": {"mode": "none", "group": "A"}}
    elif kind == "stat":
        defaults["color"] = {"mode": "fixed", "fixedColor": "#53657a"}
        options = {"reduceOptions": {"calcs": ["last"], "fields": "", "values": False},
                   "colorMode": "value", "graphMode": "none", "textMode": "auto", "orientation": "auto"}
    return {"id": identity, "title": title, "type": kind, "datasource": SOURCE, "description": description,
            "gridPos": {"x": x, "y": y, "w": width, "h": height}, "targets": queries,
            "fieldConfig": {"defaults": defaults, "overrides": []}, "options": options}


def text(identity, content, y, height):
    return {"id": identity, "title": "", "type": "text", "gridPos": {"x": 0, "y": y, "w": 24, "h": height},
            "options": {"mode": "markdown", "content": content}}


def dashboard():
    ex = expressions()
    panels = [text(1, "## UCE · Pruebas de carga k6\n"
                   "Seleccione una ejecución para consultar sus métricas y su identificación real.\n\n"
                   "Ventana móvil: **30 s**. "
                   "Sin muestras suficientes aparece **Sin datos**. Una serie anterior no confirma actividad actual. "
                   "El p95 temporal es aproximado; los umbrales y percentiles finales están en el JSON.", 0, 6)]
    for index, (key, title, unit) in enumerate((("vus", "Usuarios virtuales activos", "none"),
                                               ("rps", "Solicitudes / segundo · 30 s", "reqps"),
                                               ("p95", "Latencia HTTP p95 · 30 s", "ms"),
                                               ("errors", "Errores HTTP · 30 s", "percent"))):
        # Aggregate distributions before computing quantiles; never reduce p95 series by averaging.
        selected = BASE + ',phase=~"${phase:regex}",name=~"${endpoint:regex}"'
        total = f'sum(rate(k6_http_reqs_total{{{selected}}}[30s]))'
        failed = f'sum(rate(k6_http_reqs_total{{{selected},expected_response="false"}}[30s]))'
        live = f'count({ex["vus"]}) > 0'
        incomplete = f'sum(count_over_time(k6_http_reqs_total{{{selected}}}[30s]) < 2)'
        card = {"vus": ex["vus"], "rps": f'({total}) and on() ({live})',
                "p95": f'(1000 * histogram_quantile(0.95, sum(rate(k6_http_req_duration_seconds{{{selected}}}[30s])))) and on() ({live})',
                "errors": f'(100 * (({failed} or (0 * {total})) / ({total} > 0))) and on() ({live})'}[key]
        if key != "vus":
            card += f' unless on() ({incomplete})'
        panels.append(panel(10 + index, title, unit, [target(card, instant=True)], index * 6, 4, 6, 4, "stat"))
    panels += [panel(20, "Usuarios virtuales · ejecución seleccionada", "none", [target(ex["vus"], legend="{{testid}} · {{product_replicas}} rép.")], 0, 8),
               panel(21, "Solicitudes por segundo · por fase", "reqps", [target(ex["rps"])], 12, 8),
               panel(22, "Latencia HTTP p95 · por fase", "ms", [target(ex["p95"])], 0, 16,
                     description="Histograma nativo agregado por fase; p95 estimado de los últimos 30 s, no p95 final del JSON."),
               panel(23, "Errores HTTP · por fase", "percent", [target(ex["errors"])], 12, 16,
                     description="Solicitudes no esperadas / solicitudes observadas. No es un promedio de porcentajes."),
               panel(24, "Última muestra k6 registrada", "dateTimeAsLocal", [target(f'1000 * ({ex["last"]})', instant=True)], 0, 24, 12, 4, "stat"),
               panel(25, "Antigüedad de la última muestra", "s", [target(f'time() - ({ex["last"]})', instant=True)], 12, 24, 12, 4, "stat"),
               panel(26, "Identificación · valores del rango seleccionado", "none", [target(ex["info"], instant=True)], 0, 28, 24, 5, "table",
                     "Etiquetas reales. La presencia de un run_id histórico no significa que siga ejecutándose.")]
    panels[-1]["targets"][0]["format"] = "table"
    panels += [text(30, "### Comparación 1 vs. 3 réplicas\n"
                       "Use el validador local de resultados para abrir una pareja con salidas 0, umbrales válidos y "
                       "configuración compatible. Sin ese enlace los paneles permanecen vacíos. "
                       "La pareja validada se identifica en las leyendas de las curvas.\n\n"
                       "Las curvas conservan sus fechas reales y sus huecos. No reconstruyen históricos JSON, "
                       "no alinean artificialmente fases y no prueban capacidad máxima. "
                       "Seleccione solo sostenida o rampa; no mezcle calentamiento y preparación.", 33, 5)]
    sides, availability = [], []
    for replicas, run in ((1, "run_one"), (3, "run_three")):
        base = (f'testid=~"${{{run}:regex}}",load_scenario="scale",product_replicas="{replicas}",'
                'measurement_kind="benchmark",comparison_key=~"${compare_key:regex}"')
        sides.append(expressions(base, '${compare_phase:regex}'))
        # Freeze only eligibility at the range end; the plotted samples keep their real times.
        availability.append(f'max by(comparison_key) (last_over_time(k6_vus{{{base}}}[$__range] @ end()))')
    for index, (key, title, unit) in enumerate((("rps", "RPS", "reqps"), ("p95", "p95 HTTP", "ms"), ("errors", "Errores HTTP", "percent"))):
        queries = []
        for side, other in ((0, 1), (1, 0)):
            value = f'({sides[side][key]}) and on(comparison_key) ({availability[other]})'
            value += ' and on() (vector(${comparison_checked:raw}) == 1)'
            queries.append(target(value, "A" if side == 0 else "B"))
        panels.append(panel(40 + index, f"Comparación · {title} · fechas reales", unit, queries,
                            0 if index != 1 else 12, 38 if index < 2 else 46, 12 if index < 2 else 24))
    variables = [custom("measurement_kind", "Tipo de medición", ["benchmark", "integration_smoke"], "benchmark"),
                 variable("load_scenario", "Escenario", 'label_values(k6_vus{measurement_kind="$measurement_kind"},load_scenario)'),
                 variable("product_replicas", "Réplicas", 'label_values(k6_vus{measurement_kind="$measurement_kind",load_scenario="$load_scenario"},product_replicas)', all_values=True),
                 variable("testid", "Ejecución", 'label_values(k6_vus{measurement_kind="$measurement_kind",load_scenario="$load_scenario",product_replicas=~"$product_replicas"},testid)'),
                 variable("phase", "Fases", 'label_values(k6_http_reqs_total{testid="$testid"},phase)', multi=True, all_values=True),
                 variable("endpoint", "Endpoint", 'label_values(k6_http_reqs_total{testid="$testid"},name)', multi=True, all_values=True),
                 comparison_input("compare_key"), comparison_input("run_one"), comparison_input("run_three"),
                 custom("compare_phase", "Fase comparada", ["sustained", "ramp"], "sustained"),
                 custom("comparison_checked", "Validación local", ["0", "1"], "0", hidden=2)]
    for item in panels[1:]:
        item["gridPos"]["y"] += 2
    return {"uid": "uce-k6", "title": "UCE — Pruebas de carga k6", "description": "Etapa B · k6 local por Remote Write",
            "schemaVersion": 39, "version": 1, "editable": False, "tags": ["UCE", "k6", "etapa-b"],
            "timezone": "America/Guayaquil", "refresh": "5s", "time": {"from": "now-30m", "to": "now"},
            "graphTooltip": 1, "panels": panels, "templating": {"list": variables}, "annotations": {"list": []}}


def main():
    path = HERE / "grafana" / "dashboards" / "uce-k6.json"
    path.write_text(json.dumps(dashboard(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print("Generated only the Stage B k6 dashboard; no test was executed.")


if __name__ == "__main__":
    main()
