// @ts-check

/**
 * Read-only previews for delivery from a term-owned chart. Posting a reviewed
 * audience remains in the explicit send handlers with its retained request ID.
 * @param {import("./contracts.js").ApiClient} api
 */
export function createDeliveryPreviews(api) {
  return {
    /** @param {import("./contracts.js").ChartId} chartId @param {import("./contracts.js").ChartAudience} audience @param {import("./contracts.js").MailProvider} mailProvider @returns {Promise<import("./contracts.js").StudentChartPreview>} */
    studentCharts(chartId, audience, mailProvider) {
      const query = new URLSearchParams({ audience, mail_provider: mailProvider });
      return api(`/api/seating/${encodeURIComponent(chartId)}/student-email?${query}`);
    },
    /** @param {import("./contracts.js").ChartId} chartId @param {import("./contracts.js").InvitationAudience} audience @returns {Promise<import("./contracts.js").InvitationPreview>} */
    invitations(chartId, audience) {
      return api(`/api/seating/${encodeURIComponent(chartId)}/gmail-send?${new URLSearchParams({ audience })}`);
    },
    /** @param {import("./contracts.js").ChartId} chartId @param {import("./contracts.js").InvitationAudience} audience @returns {Promise<import("./contracts.js").MailMergePreview>} */
    mailMerge(chartId, audience) {
      return api(`/api/seating/${encodeURIComponent(chartId)}/mail-merge?${new URLSearchParams({ audience })}`);
    },
  };
}
