/**
 * PICTURE-SEARCH: the pages and pictures nearest the Inbox's search words, as a strip of small pictures above the list:
 * first what Faculty's own words about a picture say (a printed page's questions, a bank question, a notes section), then
 * what it has no words about and only looks like them (an unidentified scan, a file a student sent). A printed page and
 * its scanned copies are one picture, which says how many scans there are. Nearest is never a finding: each opens to be read.
 */
import { viewLink } from "./file-viewer.js";
import { disclosureMarkup } from "./ui.js";

/** A search worth asking pictures about: words that describe something, not a job ID or a filename. @param {string} query */
export const describesAPicture = (query) => /[a-z]{3}/i.test(query) && !/^[0-9a-f-]{8,}$/i.test(query) && !/\.(pdf|png|jpe?g|csv|xlsx?|eml)$/i.test(query);

/**
 * @param {{query: string, like?: string, results: any[]} | null} found what /api/picture-search answered for the search on screen
 * (`like`: the name of the instructor's own file it was searched by, instead of words)
 * @param {(value: unknown) => string} escape
 * @param {{open?: boolean}} [options] open unless the instructor closed it: the list found nothing, so these are the answer
 */
export function pictureStripMarkup(found, escape, { open = false } = {}) {
  if (!found?.results?.length) return "";
  const pages = found.results.map((hit) => {
    // A thing with a page of its own links there; a student's file has none, and opens only as itself.
    const caption = hit.link ? `<a href="${escape(hit.link)}">${escape(hit.label)}</a>` : `<span>${escape(hit.label)}</span>`;
    // The scanned copies of a printed page: how many, and the newest scan that holds one.
    const scans = hit.scans?.count ? `<a class="picture-strip-scans" href="${escape(hit.scans.link)}">${escape(`${hit.scans.count} ${hit.scans.count === 1 ? "scan" : "scans"}`)}</a>` : "";
    return `<figure class="picture-strip-page" data-patch-key="${escape(`${hit.source}:${hit.id}`)}"><a ${viewLink(hit.open, hit.label, escape, hit.open_page || 0)} title="Open ${escape(hit.label)}"><img src="${escape(hit.thumb)}" alt="${escape(hit.label)}" loading="lazy" width="150"></a><figcaption>${caption}<small>${escape(hit.where)}</small>${scans}</figcaption></figure>`;
  }).join("");
  return disclosureMarkup({ label: found.like ? `Pages and pictures like “${found.like}”` : `Pages and pictures for “${found.query}”`, hint: `${found.results.length} nearest`, key: "inbox:picture-strip", open,
    className: "picture-strip", body: `<div class="picture-strip-pages">${pages}</div>` });
}
