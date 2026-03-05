"use client";

import { MetricsProvider } from "./MetricsProvider";

export function ClientProviders({ children }: { children: React.ReactNode }) {
  return <MetricsProvider>{children}</MetricsProvider>;
}
