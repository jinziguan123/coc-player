/** 逗号分隔列表的显示与解析（中英文逗号、顿号都认）。 */
export const csv = (a?: string[]) => (a || []).join(', ')
export const parseCsv = (v: string) => v.split(/[,，、]/).map((s) => s.trim()).filter(Boolean)
