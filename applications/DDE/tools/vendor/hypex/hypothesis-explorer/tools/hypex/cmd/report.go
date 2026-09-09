package cmd

import (
	"fmt"
	"os"
	"path/filepath"

	"github.com/spf13/cobra"
)

func init() {
	cmd := &cobra.Command{
		Use:   "report",
		Short: "Generate a run report (stub)",
		Long: `Generates a report for a run. This is intentionally minimal — real
report generation is the meta-review agent's responsibility in later
phases. This stub checks that the run exists and the report directory
is present, then reports that generation is deferred.`,
		RunE: runReport,
	}

	rootCmd.AddCommand(cmd)
}

func runReport(_ *cobra.Command, _ []string) error {
	if flagRunID == "" {
		return fmt.Errorf("--run flag is required")
	}

	runDir := filepath.Join(flagRunDir, flagRunID)
	reportDir := filepath.Join(runDir, "report")

	if _, err := os.Stat(reportDir); err != nil {
		return fmt.Errorf("run %q not found or missing report/ directory", flagRunID)
	}

	fmt.Printf("Run: %s\n", flagRunID)
	fmt.Println("Report generation is deferred to the meta-review agent (Phase 3).")
	fmt.Printf("Report will be written to: %s/final.md\n", reportDir)
	return nil
}
