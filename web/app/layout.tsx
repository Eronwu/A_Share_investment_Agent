import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "A Share Investment Agent",
  description: "",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" style={{ backgroundColor: "#282a36" }}>
      <head>
        <link
          href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body style={{ margin: 0, padding: 0, overflow: "hidden", background: "#282a36" }}>
        {children}
      </body>
    </html>
  );
}
