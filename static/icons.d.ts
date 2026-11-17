/* Types of icons.js for the incremental type check (tsconfig.frontend.json): ui.js imports it, and icons.js itself is
   not type-checked. Keep in step with icons.js's two exports. */
/** Decorative SVG markup of a named pictogram (unknown names draw 'grid'). */
export function icon(name: string, className?: string): string;
/** Fill every [data-icon] under root with its pictogram. */
export function mountIcons(root?: ParentNode): void;
