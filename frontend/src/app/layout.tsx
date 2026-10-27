import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "RealEstAI — Floor plans to furnished 3D walkthroughs",
  description:
    "Upload a 2D floor plan and receive a validated, furnished, first-person 3D walkthrough. Room detection, automatic furnishing, and navigation checks happen on your own machine.",
};

export const viewport: Viewport = {
  themeColor: "#f5f1e8",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="flex min-h-full flex-col bg-ivory text-ink">{children}</body>
    </html>
  );
}
