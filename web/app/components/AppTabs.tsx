import Link from "next/link";
import { ReactNode } from "react";

export type AppTabKey = "holdings" | "watching" | "alerts" | "sim" | "sector" | "manage";

type TabItem = { key: AppTabKey; label: string; href: string };

const TAB_ITEMS: TabItem[] = [
  { key: "holdings", label: "holdings", href: "/" },
  { key: "watching", label: "watching", href: "/watching" },
  { key: "alerts", label: "alerts", href: "/alerts" },
  { key: "sim", label: "sim", href: "/sim" },
  { key: "sector", label: "sector", href: "/sector" },
  { key: "manage", label: "manage", href: "/manage" },
];

const C = {
  bg: "#282a36",
  tabBg: "#21222c",
  fg: "#f8f8f2",
  comment: "#6272a4",
  purple: "#bd93f9",
  green: "#50fa7b",
} as const;

export function AppTabs({ active, rightSlot }: { active: AppTabKey; rightSlot?: ReactNode }) {
  return (
    <div
      style={{
        display: "flex",
        background: C.tabBg,
        borderBottom: "1px solid #191a21",
        fontSize: 11,
        userSelect: "none",
      }}
    >
      {TAB_ITEMS.map((t, i) => {
        const isActive = t.key === active;
        return (
          <Link key={t.key} href={t.href} style={{ textDecoration: "none" }}>
            <div
              style={{
                padding: "5px 16px",
                background: isActive ? C.bg : C.tabBg,
                color: isActive ? C.fg : C.comment,
                borderRight: "1px solid #191a21",
                borderTop: isActive ? `2px solid ${C.purple}` : "2px solid transparent",
                display: "flex",
                alignItems: "center",
                gap: 6,
                minWidth: 130,
                cursor: "pointer",
              }}
            >
              <span style={{ fontSize: 8, color: isActive ? C.green : "#555" }}>{isActive ? "✱" : "●"}</span>
              <span>{t.label}</span>
              <span style={{ marginLeft: "auto", color: C.comment, fontSize: 10 }}>⌘{i + 1}</span>
            </div>
          </Link>
        );
      })}
      <div style={{ flex: 1 }} />
      {rightSlot}
    </div>
  );
}
