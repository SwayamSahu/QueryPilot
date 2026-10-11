"use client";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { ChartSpec, TableResult } from "@/lib/types";
import { axisFmt, fmt } from "@/lib/format";

const PALETTE = ["#a78bfa", "#60a5fa", "#34d399", "#fbbf24", "#f472b6", "#22d3ee", "#fb923c", "#c4b5fd"];
const AXIS = { stroke: "#3a4a74", tick: { fill: "#8d97b3", fontSize: 11 }, tickLine: false };

function records(table: TableResult) {
  const dims = table.columns.filter((c) => c.kind === "dimension").map((c) => c.key);
  return table.rows.map((r) => {
    const o: Record<string, unknown> = {};
    table.columns.forEach((c, i) => (o[c.key] = r[i]));
    o.__label = dims.map((d) => o[d]).join(" · ");
    return o;
  });
}

export function Chart({ spec, table }: { spec: ChartSpec; table: TableResult }) {
  const data = records(table);
  const f = spec.series[0]?.format ?? "number";
  const tip = (
    <Tooltip
      cursor={{ fill: "rgba(167,139,250,0.08)" }}
      contentStyle={{ background: "#0f162a", border: "1px solid #28365c", borderRadius: 8, fontSize: 12 }}
      labelStyle={{ color: "#e8ecf6" }}
      formatter={(v: number, name: string) => [fmt(v, f), name]}
    />
  );
  if (spec.type === "line") {
    const s = spec.series[0];
    if (s?.pivot) {
      const xs = Array.from(new Set(data.map((d) => String(d[spec.x!]))));
      const groups = Array.from(new Set(data.map((d) => String(d[s.pivot!])))).slice(0, 8);
      const pivoted = xs.map((x) => {
        const o: Record<string, unknown> = { x };
        data.filter((d) => String(d[spec.x!]) === x).forEach((d) => (o[String(d[s.pivot!])] = d[s.key]));
        return o;
      });
      return (
        <ResponsiveContainer width="100%" height={280}>
          <LineChart data={pivoted} margin={{ top: 10, right: 16, left: 4, bottom: 0 }}>
            <CartesianGrid stroke="#1c2745" vertical={false} />
            <XAxis dataKey="x" {...AXIS} />
            <YAxis {...AXIS} tickFormatter={axisFmt(f)} width={56} />
            {tip}
            <Legend wrapperStyle={{ fontSize: 11, color: "#b6bfd6" }} />
            {groups.map((g, i) => <Line key={g} dataKey={g} stroke={PALETTE[i % PALETTE.length]} strokeWidth={2} dot={{ r: 2.5 }} />)}
          </LineChart>
        </ResponsiveContainer>
      );
    }
    return (
      <ResponsiveContainer width="100%" height={280}>
        <LineChart data={data} margin={{ top: 10, right: 16, left: 4, bottom: 0 }}>
          <CartesianGrid stroke="#1c2745" vertical={false} />
          <XAxis dataKey={spec.x ?? "__label"} {...AXIS} />
          <YAxis {...AXIS} tickFormatter={axisFmt(f)} width={56} />
          {tip}
          {spec.series.length > 1 && <Legend wrapperStyle={{ fontSize: 11 }} />}
          {spec.series.map((se, i) => (
            <Line key={se.key} dataKey={se.key} name={se.label} stroke={PALETTE[i]} strokeWidth={2.2}
              dot={{ r: 3, strokeWidth: 2, fill: "#0b1120" }} activeDot={{ r: 5 }} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    );
  }
  if (spec.type === "bar" || spec.type === "grouped_bar") {
    const grouped = spec.type === "grouped_bar";
    return (
      <ResponsiveContainer width="100%" height={280}>
        <BarChart data={data} margin={{ top: 10, right: 16, left: 4, bottom: 0 }} barGap={3}>
          <CartesianGrid stroke="#1c2745" vertical={false} />
          <XAxis dataKey="__label" {...AXIS} interval={0} angle={data.length > 8 ? -25 : 0} textAnchor={data.length > 8 ? "end" : "middle"}
            height={data.length > 8 ? 60 : 30} />
          <YAxis {...AXIS} tickFormatter={axisFmt(f)} width={56} />
          {tip}
          {grouped && <Legend wrapperStyle={{ fontSize: 11, color: "#b6bfd6" }} />}
          {spec.series.map((se, i) => (
            <Bar key={se.key} dataKey={se.key} name={se.label} radius={[4, 4, 0, 0]} maxBarSize={56}
              fill={grouped ? (i === 0 ? "#60a5fa" : "#a78bfa") : "#a78bfa"} fillOpacity={grouped && i === 0 ? 0.55 : 0.9} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    );
  }
  return null;
}
