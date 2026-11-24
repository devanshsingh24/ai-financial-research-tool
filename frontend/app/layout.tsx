import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = { title: "FinSight | AI Research Terminal", description: "Local, evidence-led financial research." };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
