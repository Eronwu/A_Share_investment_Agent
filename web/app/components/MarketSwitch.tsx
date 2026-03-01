import { D } from "../theme";

export type MarketTab = "A" | "HK";

export function MarketSwitch({
  activeTab,
  onTabChange,
}: {
  activeTab: MarketTab;
  onTabChange: (t: MarketTab) => void;
}) {
  return (
    <div style={{ display: "flex", gap: 8, marginBottom: 8, alignItems: "center" }}>
      <span style={{ color: D.comment, fontSize: 12 }}>market:</span>
      {(["A", "HK"] as MarketTab[]).map((t) => (
        <button
          key={t}
          onClick={() => onTabChange(t)}
          style={{
            background: activeTab === t ? D.purple : "transparent",
            color: activeTab === t ? D.bg : D.comment,
            border: `1px solid ${activeTab === t ? D.purple : D.currentLine}`,
            borderRadius: 3,
            padding: "2px 10px",
            cursor: "pointer",
            fontFamily: "JetBrains Mono, monospace",
            fontSize: 12,
            fontWeight: 700,
          }}
          title={`Switch to ${t === "A" ? "A-share" : "HK"} market`}
        >
          {t === "A" ? "A-share" : "HK"}
        </button>
      ))}
    </div>
  );
}
