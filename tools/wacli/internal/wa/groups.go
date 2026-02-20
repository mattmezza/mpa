package wa

import (
	"context"
	"fmt"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/types"
)

func (c *Client) GetJoinedGroups(ctx context.Context) ([]*types.GroupInfo, error) {
	c.mu.Lock()
	cli := c.client
	c.mu.Unlock()
	if cli == nil || !cli.IsConnected() {
		return nil, fmt.Errorf("not connected")
	}
	return cli.GetJoinedGroups(ctx)
}

func (c *Client) SetGroupName(ctx context.Context, jid types.JID, name string) error {
	c.mu.Lock()
	cli := c.client
	c.mu.Unlock()
	if cli == nil || !cli.IsConnected() {
		return fmt.Errorf("not connected")
	}
	return cli.SetGroupName(ctx, jid, name)
}

type GroupParticipantAction string

const (
	GroupParticipantAdd     GroupParticipantAction = "add"
	GroupParticipantRemove  GroupParticipantAction = "remove"
	GroupParticipantPromote GroupParticipantAction = "promote"
	GroupParticipantDemote  GroupParticipantAction = "demote"
)

func (c *Client) UpdateGroupParticipants(ctx context.Context, group types.JID, users []types.JID, action GroupParticipantAction) ([]types.GroupParticipant, error) {
	c.mu.Lock()
	cli := c.client
	c.mu.Unlock()
	if cli == nil || !cli.IsConnected() {
		return nil, fmt.Errorf("not connected")
	}

	var a whatsmeow.ParticipantChange
	switch action {
	case GroupParticipantAdd:
		a = whatsmeow.ParticipantChangeAdd
	case GroupParticipantRemove:
		a = whatsmeow.ParticipantChangeRemove
	case GroupParticipantPromote:
		a = whatsmeow.ParticipantChangePromote
	case GroupParticipantDemote:
		a = whatsmeow.ParticipantChangeDemote
	default:
		return nil, fmt.Errorf("unknown participant action: %s", action)
	}

	return cli.UpdateGroupParticipants(ctx, group, users, a)
}

func (c *Client) GetGroupInviteLink(ctx context.Context, group types.JID, reset bool) (string, error) {
	c.mu.Lock()
	cli := c.client
	c.mu.Unlock()
	if cli == nil || !cli.IsConnected() {
		return "", fmt.Errorf("not connected")
	}
	return cli.GetGroupInviteLink(ctx, group, reset)
}

func (c *Client) JoinGroupWithLink(ctx context.Context, code string) (types.JID, error) {
	c.mu.Lock()
	cli := c.client
	c.mu.Unlock()
	if cli == nil || !cli.IsConnected() {
		return types.JID{}, fmt.Errorf("not connected")
	}
	return cli.JoinGroupWithLink(ctx, code)
}

func (c *Client) LeaveGroup(ctx context.Context, group types.JID) error {
	c.mu.Lock()
	cli := c.client
	c.mu.Unlock()
	if cli == nil || !cli.IsConnected() {
		return fmt.Errorf("not connected")
	}
	return cli.LeaveGroup(ctx, group)
}
