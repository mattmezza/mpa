package config

import (
	"os"
	"path/filepath"
)

func DefaultStoreDir() string {
	home, err := os.UserHomeDir()
	if err != nil || home == "" {
		return ".wacli"
	}
	return filepath.Join(home, ".wacli")
}
