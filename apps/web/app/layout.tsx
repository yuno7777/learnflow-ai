import type { Metadata } from "next";

import { Providers } from "@/components/providers";
import { THEME_BOOTSTRAP } from "@/components/theme";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: "LearnFlow AI", template: "%s · LearnFlow AI" },
  description:
    "Multi-tenant AI workflow automation for LMS and enterprise training: RAG knowledge bases, " +
    "AI course generation, assessment evaluation and n8n-driven automation.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* Applies the stored theme before first paint, so there is no light flash. */}
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOTSTRAP }} />
      </head>
      <body className="min-h-screen bg-background">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50
            focus:rounded focus:bg-primary focus:px-3 focus:py-2 focus:text-primary-foreground"
        >
          Skip to content
        </a>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
