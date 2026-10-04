import {InfiniteRowModelModule, ModuleRegistry, PaginationModule, themeQuartz} from 'ag-grid-community';

/** Shared AG Grid modules and visual tokens for operational overview tables. */
export const overviewGridTheme = themeQuartz.withParams({
  accentColor: '#d61f69',
  backgroundColor: 'var(--tui-background-base)',
  borderColor: 'var(--tui-border-normal)',
  borderRadius: 12,
  fontFamily: 'Inter, Arial, sans-serif',
  headerBackgroundColor: 'var(--tui-background-neutral-1)',
  headerTextColor: 'var(--tui-text-primary)',
  spacing: 8,
  textColor: 'var(--tui-text-primary)',
});

/** Register only the modules required by the shared paged overview pattern. */
export function registerOverviewGridModules(): void {
  ModuleRegistry.registerModules([InfiniteRowModelModule, PaginationModule]);
}
