import type { Metadata } from "next";
import { LegalDocument } from "../legal/LegalDocument";
import { LEGAL_DOCS } from "../legal/docs";

export const metadata: Metadata = { title: `${LEGAL_DOCS.privacy.title}` };

export default function PrivacyPage() {
  return <LegalDocument docKey="privacy" />;
}
