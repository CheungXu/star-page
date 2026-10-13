import type { Metadata } from "next";
import { LegalDocument } from "../legal/LegalDocument";
import { LEGAL_DOCS } from "../legal/docs";

export const metadata: Metadata = { title: `${LEGAL_DOCS.terms.title}` };

export default function TermsPage() {
  return <LegalDocument docKey="terms" />;
}
