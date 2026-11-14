import type { Metadata } from 'next';
import './globals.css';
import './desktop.css';
import './workbench.css';
import { APPLICATION_FULL_TITLE, APPLICATION_SUBTITLE } from '../lib/branding';
import InterfaceUpdateNotice from '../components/layout/InterfaceUpdateNotice';

export const metadata: Metadata = {
  title: APPLICATION_FULL_TITLE,
  description: APPLICATION_SUBTITLE,
  icons: {
    icon: [
      { url: '/favicon-32.png', sizes: '32x32', type: 'image/png' },
      { url: '/icon-192.png', sizes: '192x192', type: 'image/png' },
      { url: '/icon-512.png', sizes: '512x512', type: 'image/png' },
    ],
    apple: [{ url: '/apple-touch-icon.png', sizes: '180x180', type: 'image/png' }],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="es">
      <body><InterfaceUpdateNotice/>{children}</body>
    </html>
  );
}
