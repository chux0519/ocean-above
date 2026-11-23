import type { Metadata } from "next";
import { LegalPage } from "@/components/legal-page";

export const metadata: Metadata = { title: "Privacy Policy · Ocean Above" };
export const dynamic = "force-static";

export default function PrivacyPage() {
  return <LegalPage title="Privacy Policy" />;
}
