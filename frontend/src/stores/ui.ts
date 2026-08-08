import { create } from "zustand";
import { persist } from "zustand/middleware";

export type Theme = "dark" | "light";

/** Live progress of an in-flight on-demand pipeline run (SSE `stage`
 * events; cleared by the terminal `run` event). Not persisted. */
export interface PipelineProgress {
  symbol: string;
  stage: string;
}

interface UiState {
  theme: Theme;
  setTheme: (theme: Theme) => void;
  paletteOpen: boolean;
  setPaletteOpen: (open: boolean) => void;
  notificationsOpen: boolean;
  setNotificationsOpen: (open: boolean) => void;
  shortcutsOpen: boolean;
  setShortcutsOpen: (open: boolean) => void;
  // manual sidebar collapse (icon-only) to reclaim chart width on demand
  sidebarCollapsed: boolean;
  toggleSidebar: () => void;
  symbol: string;
  setSymbol: (symbol: string) => void;
  timeframe: string;
  setTimeframe: (tf: string) => void;
  /** Trade-tab favorites bar. null = "every chartable symbol" (the
   * default until the user edits); an explicit list after any edit. */
  favorites: string[] | null;
  addFavorite: (symbol: string, available: string[]) => void;
  removeFavorite: (symbol: string, available: string[]) => void;
  indicators: string[];
  toggleIndicator: (name: string) => void;
  // named indicator sets (PC.4), synced to server prefs
  indicatorTemplates: Record<string, string[]>;
  saveIndicatorTemplate: (name: string) => void;
  applyIndicatorTemplate: (name: string) => void;
  deleteIndicatorTemplate: (name: string) => void;
  /** hydrate chart prefs from the server layouts.chart blob (once, on boot) */
  hydrateChart: (chart: unknown) => void;
  showVolume: boolean;
  toggleVolume: () => void;
  logScale: boolean;
  toggleLogScale: () => void;
  showProfile: boolean;
  toggleProfile: () => void;
  lastSeenAt: number; // powers the "since you left" diff panel
  markSeen: () => void;
  runDialogOpen: boolean;
  setRunDialogOpen: (open: boolean) => void;
  pipelineProgress: PipelineProgress | null;
  setPipelineProgress: (progress: PipelineProgress | null) => void;
}

export const useUiStore = create<UiState>()(
  persist(
    (set) => ({
      theme: "light",
      setTheme: (theme) => {
        document.documentElement.dataset.theme = theme;
        set({ theme });
      },
      paletteOpen: false,
      setPaletteOpen: (paletteOpen) => set({ paletteOpen }),
      notificationsOpen: false,
      setNotificationsOpen: (notificationsOpen) => set({ notificationsOpen }),
      shortcutsOpen: false,
      setShortcutsOpen: (shortcutsOpen) => set({ shortcutsOpen }),
      sidebarCollapsed: false,
      toggleSidebar: () =>
        set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
      symbol: "BTC-USD",
      setSymbol: (symbol) => set({ symbol }),
      timeframe: "1h",
      setTimeframe: (timeframe) => set({ timeframe }),
      favorites: null,
      // edits materialize the default (all available) into an explicit
      // list first, so add/remove always operate on what the user SEES
      addFavorite: (symbol, available) =>
        set((state) => {
          const current = state.favorites ?? available;
          return current.includes(symbol)
            ? {}
            : { favorites: [...current, symbol] };
        }),
      removeFavorite: (symbol, available) =>
        set((state) => ({
          favorites: (state.favorites ?? available).filter(
            (s) => s !== symbol,
          ),
        })),
      // mockup default: EMA 10 overlay on ("Indicators (1)")
      indicators: ["EMA_10"],
      toggleIndicator: (name) =>
        set((state) => ({
          indicators: state.indicators.includes(name)
            ? state.indicators.filter((n) => n !== name)
            : [...state.indicators, name],
        })),
      indicatorTemplates: {},
      saveIndicatorTemplate: (name) =>
        set((state) => ({
          indicatorTemplates: {
            ...state.indicatorTemplates,
            [name]: [...state.indicators],
          },
        })),
      applyIndicatorTemplate: (name) =>
        set((state) => ({
          indicators: [...(state.indicatorTemplates[name] ?? state.indicators)],
        })),
      deleteIndicatorTemplate: (name) =>
        set((state) => {
          const next = { ...state.indicatorTemplates };
          delete next[name];
          return { indicatorTemplates: next };
        }),
      hydrateChart: (chart) => {
        if (!chart || typeof chart !== "object") return;
        const c = chart as Partial<UiState>;
        set((state) => ({
          indicators: Array.isArray(c.indicators) ? c.indicators : state.indicators,
          showVolume:
            typeof c.showVolume === "boolean" ? c.showVolume : state.showVolume,
          logScale: typeof c.logScale === "boolean" ? c.logScale : state.logScale,
          indicatorTemplates:
            c.indicatorTemplates && typeof c.indicatorTemplates === "object"
              ? (c.indicatorTemplates as Record<string, string[]>)
              : state.indicatorTemplates,
        }));
      },
      // mockup default: volume pane on
      showVolume: true,
      toggleVolume: () => set((state) => ({ showVolume: !state.showVolume })),
      logScale: false,
      toggleLogScale: () => set((state) => ({ logScale: !state.logScale })),
      // volume profile off by default — opt-in visual weight
      showProfile: false,
      toggleProfile: () => set((state) => ({ showProfile: !state.showProfile })),
      lastSeenAt: Date.now(),
      markSeen: () => set({ lastSeenAt: Date.now() }),
      runDialogOpen: false,
      setRunDialogOpen: (runDialogOpen) => set({ runDialogOpen }),
      pipelineProgress: null,
      setPipelineProgress: (pipelineProgress) => set({ pipelineProgress }),
    }),
    {
      name: "pro-ui",
      // v1: the Accops reskin made LIGHT the default — migrate persisted
      // sessions to it once; the toggle still persists a choice afterwards
      // v2: mockup chart defaults (volume pane + EMA 10) applied once
      version: 2,
      migrate: (persisted, version) => {
        const state = (persisted ?? {}) as Partial<UiState>;
        if (version < 1) state.theme = "light";
        if (version < 2) {
          state.showVolume = true;
          if (!state.indicators?.length) state.indicators = ["EMA_10"];
        }
        return state as UiState;
      },
      partialize: (s) => ({
        theme: s.theme,
        symbol: s.symbol,
        timeframe: s.timeframe,
        lastSeenAt: s.lastSeenAt,
        indicators: s.indicators,
        showVolume: s.showVolume,
        logScale: s.logScale,
        indicatorTemplates: s.indicatorTemplates,
        showProfile: s.showProfile,
        sidebarCollapsed: s.sidebarCollapsed,
        favorites: s.favorites,
      }),
    },
  ),
);

export function usePipelineProgress() {
  return useUiStore((s) => s.pipelineProgress);
}

export function applyPersistedTheme() {
  document.documentElement.dataset.theme = useUiStore.getState().theme;
}
