// Package cmd implements the Cobra command tree for the elo CLI.
package cmd

import (
	"fmt"
	"os"

	"github.com/spf13/cobra"
)

var (
	flagRunDir string
)

// rootCmd is the top-level command for the elo CLI.
var rootCmd = &cobra.Command{
	Use:   "elo",
	Short: "Elo rating engine for hypothesis-explorer tournaments",
	Long: `elo computes Elo ratings from match records and generates optimal
pairings for hypothesis-vs-hypothesis tournament matches.

Verbs: recompute, pair, standings

The match ledger is the single source of truth. Ratings are derived state,
deterministically recomputed from matches via 'elo recompute'.`,
	SilenceErrors: true,
	SilenceUsage:  true,
}

func init() {
	rootCmd.PersistentFlags().StringVar(&flagRunDir, "run-dir", "",
		"Path to the run directory (required)")
}

// Execute runs the root command. Called from main.
func Execute() {
	if err := rootCmd.Execute(); err != nil {
		fmt.Fprintln(os.Stderr, "Error:", err)
		os.Exit(1)
	}
}
