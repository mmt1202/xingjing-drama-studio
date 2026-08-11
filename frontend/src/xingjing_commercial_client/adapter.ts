import type {
  AcceptDeliveryInput,
  CommercialClient,
  CommercialCommandOptions,
  CommercialMilestonePage,
  CommercialOrderListQuery,
  CommercialOrderPage,
  OpenDisputeInput,
  RecordContractInput,
  ResolveDisputeInput,
  ReturnDeliveryInput,
  SettlementConfirmationInput,
  SubmitDeliveryInput,
  SubmitQuoteInput,
  VersionedCommercialOrder,
} from "./types";

interface OrderCommand {
  readonly orderId: string;
  readonly command: CommercialCommandOptions;
}

export type CommercialPageCommand =
  | (OrderCommand & {
      readonly type: "submit_quote";
      readonly input: SubmitQuoteInput;
    })
  | (OrderCommand & {
      readonly type: "accept_quote";
      readonly quoteId: string;
    })
  | (OrderCommand & {
      readonly type: "record_contract";
      readonly input: RecordContractInput;
    })
  | (OrderCommand & {
      readonly type: "submit_delivery";
      readonly milestoneId: string;
      readonly input: SubmitDeliveryInput;
    })
  | (OrderCommand & {
      readonly type: "return_delivery";
      readonly deliveryId: string;
      readonly input: ReturnDeliveryInput;
    })
  | (OrderCommand & {
      readonly type: "accept_delivery";
      readonly deliveryId: string;
      readonly input: AcceptDeliveryInput;
    })
  | (OrderCommand & {
      readonly type: "open_dispute";
      readonly milestoneId: string;
      readonly input: OpenDisputeInput;
    })
  | (OrderCommand & {
      readonly type: "resolve_dispute";
      readonly disputeId: string;
      readonly input: ResolveDisputeInput;
    })
  | (OrderCommand & {
      readonly type: "freeze_settlement";
      readonly settlementId: string;
      readonly input: SettlementConfirmationInput;
    })
  | (OrderCommand & {
      readonly type: "resume_settlement";
      readonly settlementId: string;
      readonly input: SettlementConfirmationInput;
    })
  | (OrderCommand & {
      readonly type: "pay_settlement";
      readonly settlementId: string;
      readonly input: SettlementConfirmationInput;
    });

export interface CommercialPageAdapter {
  listOrders(query?: CommercialOrderListQuery, signal?: AbortSignal): Promise<CommercialOrderPage>;
  getOrder(orderId: string, signal?: AbortSignal): Promise<VersionedCommercialOrder>;
  listMilestones(orderId: string, signal?: AbortSignal): Promise<CommercialMilestonePage>;
  execute(command: CommercialPageCommand): Promise<VersionedCommercialOrder>;
}

export function createCommercialPageAdapter(client: CommercialClient): CommercialPageAdapter {
  return {
    listOrders: (query, signal) => client.listOrders(query, signal),
    getOrder: (orderId, signal) => client.getOrder(orderId, signal),
    listMilestones: (orderId, signal) => client.listMilestones(orderId, signal),
    execute(command) {
      switch (command.type) {
        case "submit_quote":
          return client.submitQuote(command.orderId, command.input, command.command);
        case "accept_quote":
          return client.acceptQuote(command.orderId, command.quoteId, command.command);
        case "record_contract":
          return client.recordContract(command.orderId, command.input, command.command);
        case "submit_delivery":
          return client.submitDelivery(
            command.orderId,
            command.milestoneId,
            command.input,
            command.command,
          );
        case "return_delivery":
          return client.returnDelivery(
            command.orderId,
            command.deliveryId,
            command.input,
            command.command,
          );
        case "accept_delivery":
          return client.acceptDelivery(
            command.orderId,
            command.deliveryId,
            command.input,
            command.command,
          );
        case "open_dispute":
          return client.openDispute(
            command.orderId,
            command.milestoneId,
            command.input,
            command.command,
          );
        case "resolve_dispute":
          return client.resolveDispute(
            command.orderId,
            command.disputeId,
            command.input,
            command.command,
          );
        case "freeze_settlement":
          return client.freezeSettlement(
            command.orderId,
            command.settlementId,
            command.input,
            command.command,
          );
        case "resume_settlement":
          return client.resumeSettlement(
            command.orderId,
            command.settlementId,
            command.input,
            command.command,
          );
        case "pay_settlement":
          return client.paySettlement(
            command.orderId,
            command.settlementId,
            command.input,
            command.command,
          );
      }
    },
  };
}
