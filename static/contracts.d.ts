/** Retained ownership IDs are distinct, opaque strings; never parse to numbers. */
declare const owner: unique symbol;
export type OwnerId<Kind extends string> = string & { readonly [owner]: Kind };
export type DefinitionId = OwnerId<"course-definition">;
export type TermId = OwnerId<"course-term">;
export type SectionId = OwnerId<"course-section">;
export type NavigationGroupId = OwnerId<"navigation-group">;
export type SavedOfferingId = OwnerId<"saved-offering">;
export type ChartId = OwnerId<"seating-chart">;
export type EnrollmentId = OwnerId<"enrollment">;
export type InboxJobId = OwnerId<"inbox-job">;
/** Nine digits, including leading zeroes; not an enrollment or upload token. */
export type Uin = OwnerId<"uin">;

export type ApiClient = ReturnType<typeof import("./request.js").createApiClient>;
export type MailProvider = "default" | "gmail";
export type InvitationAudience = "unsent" | "pending";
export type ChartAudience = "unsent" | "all";
export interface DeliveryChart {
  id: ChartId;
  title: string;
  room_name: string;
}
export interface GmailAccount { connected: boolean; email?: string }
export interface MailConfiguration { provider?: string; gmail?: GmailAccount }
export interface InvitationRecipient {
  name: string;
  email: string;
  section_number: number;
  kind: "seat" | "dr";
  delivery: string;
  subject?: string;
}
export interface StudentChartRecipient {
  name: string;
  email: string;
  section_number: number;
  enrollment_id: EnrollmentId;
  section_id: SectionId;
  seat: string;
}
export interface SkippedRecipient { name: string; section_number: number; reason: string }
export interface AudiencePreview<Recipient> {
  chart_id: ChartId;
  fingerprint: string;
  eligible_count: number;
  skipped_count: number;
  recipients: Recipient[];
  skipped: SkippedRecipient[];
  subject: string;
}
export interface InvitationPreview extends AudiencePreview<InvitationRecipient> {
  audience: InvitationAudience;
  can_send: boolean;
  sender: string;
  gmail: GmailAccount;
}
export interface MailMergePreview extends AudiencePreview<InvitationRecipient> {
  audience: InvitationAudience;
  filename: string;
}
export interface StudentChartPreview extends AudiencePreview<StudentChartRecipient> {
  audience: ChartAudience;
  mail_provider: MailProvider;
  mail: MailConfiguration;
  can_send: boolean;
  sender: string;
  history: { name: string; email: string; seat: string; status: string; created_at: string; error: string }[];
}
