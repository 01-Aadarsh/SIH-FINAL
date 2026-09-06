import type { Metadata } from "next";
import "./globals.css";
import { FontScaleSync } from "@/components/FontScaleSync";

export const metadata: Metadata = {
  title: "IP-SAKTI Sahayak",
  description:
    "Source-cited AI assistant for Ayurveda-related IP and regulatory questions.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="bg-paper text-ink antialiased">
        <FontScaleSync />
        {children}
      </body>
    </html>
  );
}
