package main

import (
	"context"
	"fmt"
	"os"
	"text/tabwriter"
	"time"

	"github.com/spf13/cobra"
	"github.com/steipete/wacli/internal/out"
)

func newChatsCmd(flags *rootFlags) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "chats",
		Short: "List chats from the local DB",
	}
	cmd.AddCommand(newChatsListCmd(flags))
	cmd.AddCommand(newChatsShowCmd(flags))
	return cmd
}

func newChatsListCmd(flags *rootFlags) *cobra.Command {
	var query string
	var limit int
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List chats",
		RunE: func(cmd *cobra.Command, args []string) error {
			ctx, cancel := withTimeout(context.Background(), flags)
			defer cancel()

			a, lk, err := newApp(ctx, flags, false, false)
			if err != nil {
				return err
			}
			defer closeApp(a, lk)

			chats, err := a.DB().ListChats(query, limit)
			if err != nil {
				return err
			}
			if flags.asJSON {
				return out.WriteJSON(os.Stdout, chats)
			}

			w := tabwriter.NewWriter(os.Stdout, 2, 4, 2, ' ', 0)
			fmt.Fprintln(w, "KIND\tNAME\tJID\tLAST")
			for _, c := range chats {
				name := c.Name
				if name == "" {
					name = c.JID
				}
				fmt.Fprintf(w, "%s\t%s\t%s\t%s\n", c.Kind, truncate(name, 28), c.JID, c.LastMessageTS.Local().Format("2006-01-02 15:04:05"))
			}
			_ = w.Flush()
			return nil
		},
	}
	cmd.Flags().StringVar(&query, "query", "", "search query")
	cmd.Flags().IntVar(&limit, "limit", 50, "limit")
	return cmd
}

func newChatsShowCmd(flags *rootFlags) *cobra.Command {
	var jid string
	cmd := &cobra.Command{
		Use:   "show",
		Short: "Show one chat",
		RunE: func(cmd *cobra.Command, args []string) error {
			if jid == "" {
				return fmt.Errorf("--jid is required")
			}
			ctx, cancel := withTimeout(context.Background(), flags)
			defer cancel()

			a, lk, err := newApp(ctx, flags, false, false)
			if err != nil {
				return err
			}
			defer closeApp(a, lk)

			c, err := a.DB().GetChat(jid)
			if err != nil {
				return err
			}
			if flags.asJSON {
				return out.WriteJSON(os.Stdout, c)
			}
			fmt.Fprintf(os.Stdout, "JID: %s\nKind: %s\nName: %s\nLast: %s\n", c.JID, c.Kind, c.Name, c.LastMessageTS.Local().Format(time.RFC3339))
			return nil
		},
	}
	cmd.Flags().StringVar(&jid, "jid", "", "chat JID")
	return cmd
}
