import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "NETSOL RAG",
  description: "Source-grounded RAG workspace for the NETSOL corpus"
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
