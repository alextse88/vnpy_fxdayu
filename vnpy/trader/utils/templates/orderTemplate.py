from vnpy.trader.app.ctaStrategy import CtaTemplate
from vnpy.trader.vtObject import VtOrderData, VtTickData
from vnpy.trader.vtConstant import *
from vnpy.trader.language import constant
from vnpy.trader.app.ctaStrategy import ctaBase
from datetime import datetime, timedelta
from collections import Iterable
import numpy as np
import logging


STATUS_FINISHED = set(constant.STATUS_FINISHED)
STATUS_TRADE_POSITIVE = {constant.STATUS_PARTTRADED, constant.STATUS_ALLTRADED}

STATUS_INIT = "init"


ORDERTYPE_MAP = {
    constant.OFFSET_OPEN: {
        constant.DIRECTION_LONG: ctaBase.CTAORDER_BUY,
        constant.DIRECTION_SHORT: ctaBase.CTAORDER_SHORT
    },
    constant.OFFSET_CLOSE: {
        constant.DIRECTION_LONG: ctaBase.CTAORDER_COVER,
        constant.DIRECTION_SHORT: ctaBase.CTAORDER_SELL
    }
}


DIRECTION_MAP = {
    ctaBase.CTAORDER_BUY: constant.DIRECTION_LONG,
    ctaBase.CTAORDER_COVER: constant.DIRECTION_LONG,
    ctaBase.CTAORDER_SELL: constant.DIRECTION_SHORT,
    ctaBase.CTAORDER_SHORT: constant.DIRECTION_SHORT
}


OFFSET_MAP = {
    ctaBase.CTAORDER_BUY: constant.OFFSET_OPEN,
    ctaBase.CTAORDER_COVER: constant.OFFSET_CLOSE,
    ctaBase.CTAORDER_SELL: constant.OFFSET_CLOSE,
    ctaBase.CTAORDER_SHORT: constant.OFFSET_OPEN
}

LINK_TAG = {
    constant.DIRECTION_LONG: 1,
    constant.DIRECTION_SHORT: -1
}


def aggreatePacks(packs, name, func):
    l = []
    for pack in packs:
        if pack.order:
            l.append(getattr(pack.order, name))
    return func(l)


def showOrder(order, *params):
    if not params:
        return "VtOrder(%s)" % ", ".join("%s=%s" % item for item in order.__dict__.items())
    else:
        return "VtOrder(%s)" % ", ".join("%s=%s" % (key, getattr(order, key, None)) for key in params)


class OrderPack(object):

    def __init__(self, vtOrderID):
        self.vtOrderID = vtOrderID
        self.order = None
        self.info = {}
        self.trades = {}
        self.tracks = []

    def addTrack(self, name, value=None):
        self.tracks.append(name)
        if value is not None:
            self.info[name] = value

    def removeTrack(self, name):
        self.tracks.remove(name)


class TimeLimitOrderInfo:


    TYPE = "_TimeLimitOrderInfo"

    def __init__(self, vtSymbol, orderType, volume, price, expire):
        self.vtSymbol = vtSymbol
        self.orderType = orderType
        self.price = price
        self.expire = expire
        self.volume = volume
        self.vtOrderIDs = set()
        self.closedOrderIDs = set()
        self.inValidOrderIDs = set()
    
    def add(self, vtOrderID):
        self.vtOrderIDs.add(vtOrderID)
    
    def remove(self, vtOrderID):
        if vtOrderID in self.vtOrderIDs:
            self.vtOrderIDs.remove(vtOrderID)
            self.closedOrderIDs.add(vtOrderID)

    def finish(self, op):
        if op.vtOrderID in self.vtOrderIDs:
            self.vtOrderIDs.remove(op.vtOrderID)
            if op.order.tradedVolume:
                self.closedOrderIDs.add(op.vtOrderID)
            else:
                self.inValidOrderIDs.add(op.vtOrderID)

    def __str__(self):
        return "%s(vtSymbol=%s, orderType=%s, price=%s, volume=%s, expire=%s)" % (
            self.TYPE, self.vtSymbol, self.orderType, self.price, self.volume, self.expire
        )

class ComposoryOrderInfo(TimeLimitOrderInfo):

    TYPE = "_ComposoryOrderInfo"

    CLOSE_AFTER_FINISH = "_CPO_CAF"
    CPO_CLOSED = "_CPO_CLOSE"

    def __init__(self, vtSymbol, orderType, volume, expire):
        super(ComposoryOrderInfo, self).__init__(vtSymbol, orderType, volume, None, expire)


class AutoExitInfo(object):

    TYPE = "_AutoExitInfo"

    TP_TAG = "_TP_Tag"
    TP_CANCEL_TAG = "_TP_CANCEL"

    def __init__(self, op, stoploss=None, takeprofit=None):
        self.originID = op.vtOrderID if isinstance(op, OrderPack) else op
        self.stoploss = stoploss
        self.takeprofit = takeprofit
        self.closeOrderIDs = set()
        self.tpOrderIDs = set()
        self.slOrderIDs = set()
        self.check_tp = True

    def __str__(self):
        return "%s(originID=%s, stoploss=%s, takeprofit=%s)" % (self.TYPE, self.originID, self.stoploss, self.takeprofit)


class RependingOrderInfo(object):

    TYPE = "_RependingOrderInfo"

    TAG = "_RPD_TAG"
    ORIGIN = "_RPD_ORIGIN"
    REPENDED = "_RPD_REPENDED"

    def __init__(self, originID, volume=None, price=None):
        self.originID = originID
        self.rependedIDs = set()
        self.volume = volume
        self.price = price


class ConditionalOrderClose(object):

    TYPE = "_ConditionalOrderClose"

    def __init__(self, originID, expire_at, targetProfit=None):
        self.originID = originID
        self.expire_at = expire_at
        self.targetProfit = targetProfit
    
    def __str__(self):
        return "%s(originID=%s, expire_at=%s, targetProfit=%s)" % (self.TYPE, self.originID, self.expire_at, self.targetProfit)


class AssembleOrderInfo(object):

    TYPE = "_AssembleOrderInfo"
    TAG = "_AssembleTag"
    CHILD = "_AssembleChild"
    ORIGIN = "_AssembleOrigin"

    def __init__(self):
        self.originID = None
        self.childIDs = set()
    
    def setChild(self, op):
        self.childIDs.add(op)
        op.info[self.TYPE] = self
        op.info[self.TAG] = self.CHILD
    
    def setOrigin(self, op):
        assert not self.originID, "AssempleOrderInfo.originID already exist."
        self.originID = op.vtOrderID
        op.info[self.TYPE] = self
        op.info[self.TAG] = self.ORIGIN
    

class StepOrderInfo(object):
    TYPE = "_StepOrderInfo"

    def __init__(self, orderType, vtSymbol, price, volume, step, expire_at, wait=0):
        self.orderType = orderType
        self.vtSymbol = vtSymbol
        self.price = price
        self.volume = volume
        self.step = step
        self.expire_at = expire_at
        self.wait = wait
        self.nextSendTime = datetime.fromtimestamp(86400)
        self.vtOrderIDs = set()
        self.finishedOrderIDs = set()
        self.invalidOrderIDs = set()
    
    def add(self, op):
        self.vtOrderIDs.add(op.vtOrderID)
    
    def finish(self, op):
        if op.vtOrderID in self.vtOrderIDs:
            self.vtOrderIDs.remove(op.vtOrderID)
            if op.order.tradedVolume:
                self.finishedOrderIDs.add(op.vtOrderID)
            else:
                self.invalidOrderIDs.add(op.vtOrderID)

class DepthOrderInfo(StepOrderInfo):

    TYPE = "_DepthOrderInfo"

    def __init__(self, orderType, vtSymbol, price, volume, depth, expire_at, wait=0):
        super().__init__(orderType, vtSymbol, price, volume, None, expire_at, wait)
        assert isinstance(depth, int) and (depth > 0)
        self.depth = depth
        self.keys = []
        direction = DIRECTION_MAP[orderType]
        self.direction = 1
        if direction == constant.DIRECTION_LONG:
            self.direction = 1
            for i in range(depth):
                self.keys.append(("askPrice%d" % (i+1), "askVolume%d" % (i+1)))
        elif direction == constant.DIRECTION_SHORT:
            self.direction = -1
            for i in range(depth):
                self.keys.append(("bidPrice%d" % (i+1), "bidVolume%d" % (i+1)))

    def isPriceExecutable(self, price):
        return price and ((self.price - price)*self.direction >= 0)

class OrderTemplate(CtaTemplate):

    
    _CLOSE_TAG = "_CLOSE"
    _OPEN_TAG = "_OPEN"
    _EXPIRE_AT = "_EXPIRE_AT"
    _FINISH_TAG = "_FINISH_TAG"
    _CANCEL_TAG = "_CANCEL_TAG"

    COMPOSORY_EXPIRE = 5
    NDIGITS = 4
    PRICE_NDIGITS = 3
    UPPER_LIMIT = 1.02
    LOWER_LIMIT = 0.98


    def __init__(self, ctaEngine, setting):
        super(OrderTemplate, self).__init__(ctaEngine, setting)
        self._ORDERTYPE_LONG = {ctaBase.CTAORDER_BUY, ctaBase.CTAORDER_COVER}
        self._ORDERTYPE_SHORT = {ctaBase.CTAORDER_SELL, ctaBase.CTAORDER_SHORT}
        self._orderPacks = {}
        self._stopOrders = {}
        self._autoExitInfo = {}
        self._trades = {}
        self._currentTime = datetime(2000, 1, 1)
        self._tickInstance = {}
        self._barInstance = {}
        self._order_costum_callbacks = {}
        self._infoPool = {
            TimeLimitOrderInfo.TYPE: {},
            ComposoryOrderInfo.TYPE: {},
            ConditionalOrderClose.TYPE: {},
            StepOrderInfo.TYPE: {},
            DepthOrderInfo.TYPE: {}
        }

        self._ComposoryClosePool = {}

        self.registerOrderCostumCallback(TimeLimitOrderInfo.TYPE, self.onTimeLimitOrder)
        self.registerOrderCostumCallback(ComposoryOrderInfo.TYPE, self.onComposoryOrder)
        self.registerOrderCostumCallback(RependingOrderInfo.TYPE, self.onRependingOrder)
        self.registerOrderCostumCallback(AutoExitInfo.TP_TAG, self.onTakeProfitPending)
        self.registerOrderCostumCallback(StepOrderInfo.TYPE, self.onStepOrder)
    
    def registerOrderCostumCallback(self, co_type, callback):
        self._order_costum_callbacks[co_type] = callback

    def unregisterOrderCostumCallback(self, co_type):
        if co_type in self._order_costum_callbacks:
            del self._order_costum_callbacks[co_type]
    
    def setOrderPool(self, op, *names):
        if isinstance(op, OrderPack):
            vtOrderID = op.vtOrderID
        elif isinstance(op, str):
            vtOrderID = op
            if vtOrderID in self._orderPacks:
                op = self._orderPacks[vtOrderID]
            else:
                return False
        else:
            return False
        

        if names:
            for name in names:
                self._orderPool[name][op.vtOrderID] = op
            return True
        else:
            return False
        
    def rmOrderFromPool(self, op, *names):
        if isinstance(op, str):
            op = self._orderPacks.get(op, None)

        if not isinstance(op, OrderPack):
            return False
        
        if names:
            for name in names:
                self._orderPool[name].pop(op.vtOrderID, None)
            return True
        else:
            return False

    def onOrder(self, order):
        if order.status == constant.STATUS_UNKNOWN:
            self.mail("%s" % order.__dict__)

        try:
            op = self._orderPacks[order.vtOrderID]
        except KeyError:
            return
        else:
            if op.info.get(self._FINISH_TAG, False):
                return
            op.order = order
        
        for name in op.tracks:
            try:
                method = self._order_costum_callbacks[name]
            except KeyError:
                continue
            else:
                method(op)

        self.onOrderPack(op)

        if op.order.status in STATUS_FINISHED:
            op.info[self._FINISH_TAG] = True
        

    def onOrderPack(self, op):
        pass

    def onTrade(self, trade):
        op = self._orderPacks.get(trade.vtOrderID, None)
        if op:
            op.trades[trade.vtTradeID] = trade
            self._trades[trade.vtTradeID] = trade
    
    def _round(self, value):
        return round(value, self.NDIGITS)
    
    def makeOrder(self, orderType, vtSymbol, price, volume, priceType=constant.PRICETYPE_LIMITPRICE, stop=False, **info):
        volume = self._round(volume)
        assert volume > 0
        price = self.adjustPrice(vtSymbol, price, "send order")
        assert price > 0
        vtOrderIDList = self.sendOrder(orderType, vtSymbol, price, volume, priceType, stop)
        logging.debug("%s | makeOrder: %s, %s, %s, %s | into: %s", self.currentTime, orderType, vtSymbol, price, volume, info)

        packs = []
        for vtOrderID in vtOrderIDList:
            op = OrderPack(vtOrderID)
            op.info.update(info)
            self._orderPacks[vtOrderID] = op
            packs.append(op)
            order = VtOrderData()
            order.vtOrderID = vtOrderID
            order.vtSymbol = vtSymbol
            order.price = price
            order.totalVolume = volume
            order.priceType = priceType
            order.status = STATUS_INIT
            order.direction = DIRECTION_MAP[orderType]
            order.offset = OFFSET_MAP[orderType]
            order.datetime = self.currentTime
            op.order = order
        return packs

    def composoryClose(self, op, expire=None):
        if expire is None:
            expire = self.COMPOSORY_EXPIRE
        order = op.order
        if order.offset == constant.OFFSET_OPEN:
            if order.direction == constant.DIRECTION_LONG:
                orderType = ctaBase.CTAORDER_SELL
            elif order.direction == constant.DIRECTION_SHORT:
                orderType = ctaBase.CTAORDER_COVER
            else:
                raise ValueError("Invalid direction: %s" % order.direction)
        else:
            raise ValueError("Invalid offset: %s" % order.offset)
        if order.status not in constant.STATUS_FINISHED:
            self.cancelOrder(order.vtOrderID)
        self.addComposoryPool(op)
        op.info[ComposoryOrderInfo.CPO_CLOSED] = True
        logging.info("%s | setComposoryClose on %s | info: %s", self.currentTime, showOrder(op.order), op.info)
    
    def addComposoryPool(self, op):
        if ComposoryOrderInfo.CPO_CLOSED in op.info:
            return
        pool = self._ComposoryClosePool.setdefault(op.order.vtSymbol, {}).setdefault(op.order.direction, {})
        pool.setdefault(self._OPEN_TAG, set()).add(op.vtOrderID)
        pool.setdefault(ComposoryOrderInfo.TYPE, set())

    def checkComposoryCloseOrders(self, vtSymbol):
        if vtSymbol not in self._ComposoryClosePool:
            return
        for direction, pool in list(self._ComposoryClosePool[vtSymbol].items()):
            if self.checkComposoryClose(vtSymbol, direction, pool):
                self._ComposoryClosePool[vtSymbol].pop(direction, None)

    def checkComposoryClose(self, vtSymbol, direction, pool):
        if direction == constant.DIRECTION_LONG:
            orderType = ctaBase.CTAORDER_SELL
        elif direction == constant.DIRECTION_SHORT:
            orderType = ctaBase.CTAORDER_COVER
        totalOpened = 0
        closedVolume = 0
        lockedVolume = 0
        openAllFinished = True
        for op in self.iterValidOrderPacks(*pool[self._OPEN_TAG]):
            if not op.order.status in STATUS_FINISHED:
                openAllFinished = False
            totalOpened += op.order.tradedVolume
            for closeOP in self.listCloseOrderPack(op):
                closedVolume += closeOP.order.tradedVolume
                if closeOP.order.status not in STATUS_FINISHED:
                    lockedVolume += closeOP.order.totalVolume - closeOP.order.tradedVolume 
                    if not self.isComposory(closeOP):
                        self.cancelOrder(closeOP.vtOrderID)
        for cpo in pool[ComposoryOrderInfo.TYPE]:
            for closeOP in self.iterValidOrderPacks(*cpo.vtOrderIDs):
                closedVolume += closeOP.order.tradedVolume
                if closeOP.order.status not in STATUS_FINISHED:
                    lockedVolume += closeOP.order.totalVolume - closeOP.order.tradedVolume 
            
            for closeOP in self.iterValidOrderPacks(*cpo.closedOrderIDs):
                closedVolume += closeOP.order.tradedVolume
        unlockedVolume = self._round(totalOpened - closedVolume -lockedVolume)
        if unlockedVolume > 0 :
            cpo = self.composoryOrder(orderType, vtSymbol, unlockedVolume, self.COMPOSORY_EXPIRE)
            pool[ComposoryOrderInfo.TYPE].add(cpo)
        if self._round(totalOpened - closedVolume) <= 0 and openAllFinished:
            return True
        else:
            return False            

    def closeAfterFinish(self, op):
        if op.status in STATUS_FINISHED:
            self.composoryClose(op)

    def closeOrder(self, op, price, volume=None, priceType=constant.PRICETYPE_LIMITPRICE, cover=False, **info):
        
        order = op.order
        orderType = self.getCloseOrderType(op.order)

        unlockedVolume = self.orderUnlockedVolume(op)

        if volume is None:
            volume = unlockedVolume
        else:
            if volume > unlockedVolume:
                volume = unlockedVolume
        if volume > 0:
            logging.info("%s | close order: %s | send", self.currentTime, op.vtOrderID)
            packs = self.makeOrder(orderType, order.vtSymbol, price, volume, constant.PRICETYPE_LIMITPRICE, **info)
            for pack in packs:
                self.link(op, pack)
        else:
            logging.warning("%s | close order: %s | unlocked volume = %s <= 0, do nothing", self.currentTime, op.vtOrderID, volume)
            packs = []
        
        if cover and (self._CLOSE_TAG in op.info):
            for pack in self.iterValidOrderPacks(*op.info[self._CLOSE_TAG]):
                if pack.order.status in STATUS_FINISHED:
                    continue
                self.rependOrder(pack, price=price)
        return packs

    def rependOrder(self, op, volume=None, price=None, callback=None, **info):
        if op.order.status == constant.STATUS_ALLTRADED:
            return 
        
        roi = RependingOrderInfo(op.vtOrderID, volume, price)

        if not callback:
            callback = roi.TYPE
        op.addTrack(callback)
        op.info[roi.TYPE] = roi
        op.info[roi.TAG] = roi.ORIGIN
        if op.order.status in {constant.STATUS_CANCELLED, constant.STATUS_REJECTED}:
            method = self._order_costum_callbacks[callback]
            method(op)
        else:
            self.cancelOrder(op.vtOrderID)

        return roi
    
    def onRependingOrder(self, op):
        if op.order.status not in {constant.STATUS_CANCELLED, constant.STATUS_REJECTED}:
            return
        order = op.order
        roi = op.info[RependingOrderInfo.TYPE]
        if roi.volume and (roi.volume <= order.totalVolume - order.tradedVolume):
            volume = roi.volume
        else:
            volume = order.totalVolume - order.tradedVolume
        if volume <= 0:
            return
        
        if self.isCloseOrder(op):
            openOP = self.findOpenOrderPack(op)
            if openOP:
                unlocked = self.orderUnlockedVolume(openOP)
                if volume > unlocked:
                    volume = unlocked
            if volume <= 0:
                return
            if roi.price:
                for pack in self.closeOrder(openOP, roi.price, volume):
                    roi.rependedIDs.add(pack.vtOrderID)
            else:
                for vtOrderID in self.composoryOrder(
                    ORDERTYPE_MAP[order.offset][order.direction],
                    order.vtSymbol, volume, self.COMPOSORY_EXPIRE
                ).vtOrderIDs:
                    roi.rependedIDs.add(vtOrderID)
        else:
            if roi.price:
                for pack in self.makeOrder(
                    ORDERTYPE_MAP[order.offset][order.direction],
                    order.vtSymbol,
                    roi.price,
                    volume
                ):
                    roi.rependedIDs.add(pack.vtOrderID)
            else:
                for vtOrderID in self.composoryOrder(
                    ORDERTYPE_MAP[order.offset][order.direction],
                    order.vtSymbol, volume, self.COMPOSORY_EXPIRE
                ).vtOrderIDs:
                    roi.rependedIDs.add(vtOrderID)
            
    def link(self, openOP, closeOP):
        assert openOP.order.offset == constant.OFFSET_OPEN
        assert closeOP.order.offset == constant.OFFSET_CLOSE
        assert LINK_TAG[openOP.order.direction] + LINK_TAG[closeOP.order.direction] == 0
        openOP.info.setdefault(self._CLOSE_TAG, set()).add(closeOP.vtOrderID)
        closeOP.info[self._OPEN_TAG] = openOP.vtOrderID

    def orderClosedVolume(self, op):
        if op.info.get(ComposoryOrderInfo.CPO_CLOSED, False):
            return op.order.tradedVolume
        if not isinstance(op, OrderPack):
            op = self._orderPacks[op]
        if self._CLOSE_TAG not in op.info:
            return 0
        return self._round(self.aggOrder(op.info[self._CLOSE_TAG], "tradedVolume", sum))

    def orderLockedVolume(self, op):
        if op.info.get(ComposoryOrderInfo.CPO_CLOSED, False):
            return op.order.tradedVolume
        if not isinstance(op, OrderPack):
            op = self._orderPacks[op]
        if self._CLOSE_TAG not in op.info:
            return 0
        
        locked = 0

        for cop in self.iterValidOrderPacks(*op.info[self._CLOSE_TAG]):
            if cop.order.status in STATUS_FINISHED:
                locked += cop.order.tradedVolume
            else:
                locked += cop.order.totalVolume
        return self._round(locked)

    def orderUnlockedVolume(self, op):
        return self._round(op.order.tradedVolume - self.orderLockedVolume(op))

    def removeOrderPack(self, vtOrderID):
        del self._orderPacks[vtOrderID]
    
    def timeLimitOrder(self, orderType, vtSymbol, limitPrice, volume, expire):
        tlo = TimeLimitOrderInfo(vtSymbol, orderType, volume, limitPrice, expire)
        return self.sendTimeLimit(tlo)

    def sendTimeLimit(self, tlo):
        assert isinstance(tlo, TimeLimitOrderInfo)
        logging.info("%s | send TimeLimitOrder | %s", self.currentTime, tlo)
        packs = self.makeOrder(tlo.orderType, tlo.vtSymbol, tlo.price, tlo.volume)
        for op in packs:
            tlo.add(op.vtOrderID)
            op.info[self._EXPIRE_AT] = self.currentTime + timedelta(seconds=tlo.expire)
            op.addTrack(tlo.TYPE, tlo)
        self._infoPool[TimeLimitOrderInfo.TYPE][id(tlo)] = tlo
        return tlo

    def onTimeLimitOrder(self, op):
        tlo = op.info[TimeLimitOrderInfo.TYPE]
        if op.order.status in STATUS_FINISHED:
            tlo.finish(op)
            logging.info("%s | TimeLimitOrderFinished | %s | %s", self.currentTime, tlo, op.order.__dict__)
        elif self.checkOrderExpire(op):
            self.cancelOrder(op.vtOrderID)
            logging.info("%s | Cancel exceeded timeLimitOrder | %s | %s", self.currentTime, tlo, op.order.__dict__)

    def checkTimeLimitOrders(self):
        pool = self._infoPool[TimeLimitOrderInfo.TYPE]
        for tlo in list(pool.values()):
            for op in self.iterValidOrderPacks(*tlo.vtOrderIDs):
                self.onTimeLimitOrder(op)
            if not tlo.vtOrderIDs:
                pool.pop(id(tlo))
    
    def checkComposoryOrders(self):
        pool = self._infoPool[ComposoryOrderInfo.TYPE]
        for cpo in list(pool.values()):
            for op in self.iterValidOrderPacks(*cpo.vtOrderIDs):
                self.onComposoryOrder(op, True)
            if not cpo.vtOrderIDs:
                pool.pop(id(cpo))
    
    def sendComposory(self, cpo):
        assert isinstance(cpo, ComposoryOrderInfo)
        price = self.getExecPrice(cpo.vtSymbol, cpo.orderType)
        if price is None:
            return None
        volume = cpo.volume - self.aggOrder(cpo.vtOrderIDs, "totalVolume", sum) - self.aggOrder(cpo.closedOrderIDs, "tradedVolume", sum)
        if volume <= 0:
            logging.warning("%s | composory unlocked volume = %s | %s", self.currentTime, volume, cpo)
            return cpo
        logging.info("%s | send composory | %s", self.currentTime, cpo)
        packs = self.makeOrder(cpo.orderType, cpo.vtSymbol, price, volume)
        for op in packs:
            cpo.add(op.vtOrderID)
            op.info[self._EXPIRE_AT] = self.currentTime + timedelta(seconds=cpo.expire)
            op.addTrack(cpo.TYPE, cpo)
        self._infoPool[ComposoryOrderInfo.TYPE][id(cpo)] = cpo
        return cpo

    def composoryOrder(self, orderType, vtSymbol, volume, expire):
        cpo = ComposoryOrderInfo(vtSymbol, orderType, volume, expire)
        return self.sendComposory(cpo)
    
    def onComposoryOrder(self, op, repend=False):
        allTraded = op.order.status == constant.STATUS_ALLTRADED
        removed = op.order.status in {constant.STATUS_CANCELLED, constant.STATUS_REJECTED}
        cpo = op.info[ComposoryOrderInfo.TYPE]
        if allTraded:
            cpo.finish(op)
            logging.info("%s | composory order finished | %s | %s", self.currentTime, cpo, cpo.closedOrderIDs)
        else:
            if not removed:
                if self.checkOrderExpire(op):
                    logging.info("%s | %s | composory order not finish in timelimit, cancel then resend", self.currentTime, cpo)
                    self.cancelOrder(op.vtOrderID)
            elif repend:
                if self.isCloseOrder(op):
                    if self.orderUnlockedVolume(self.findOpenOrderPack(op)) <= 0:
                        return
                cpo.finish(op)
                self.sendComposory(cpo)
                if self.isCloseOrder(op):
                    openPack = self.findOpenOrderPack(op)
                    vtOrderIDs = cpo.vtOrderIDs
                    for vtOrderID in vtOrderIDs:
                        closePack = self._orderPacks[vtOrderID]
                        self.link(openPack, closePack) 
            
    def checkOrderExpire(self, op):
        return op.info[self._EXPIRE_AT] <= self.currentTime
    
    def setAutoExit(self, op, stoploss=None, takeprofit=None, cover=False):
        if stoploss is not None:
            stoploss = self.adjustPrice(op.order.vtSymbol, stoploss, "stoploss")
            assert stoploss > 0
        if takeprofit is not None:
            takeprofit = self.adjustPrice(op.order.vtSymbol, takeprofit, "takeprofit")
            assert takeprofit > 0
        if AutoExitInfo.TYPE not in op.info:
            ae = AutoExitInfo(op, stoploss, takeprofit)
            op.info[ae.TYPE] = ae
        else:
            ae = op.info[AutoExitInfo.TYPE]
            if stoploss or cover:
                ae.stoploss = stoploss
            if takeprofit or cover:
                ae.takeprofit = takeprofit
        if ae.stoploss or ae.takeprofit:
            self._autoExitInfo[op.vtOrderID] = op
            logging.info("%s | %s | setAutoExit", self.currentTime, ae)
        elif op.vtOrderID in self._autoExitInfo:
            self._autoExitInfo.pop(op.vtOrderID)
            logging.info("%s | %s | cancelAutoExit", self.currentTime, ae)
        return ae

    def execAutoExit(self, origin, ask, bid, check_tp=False):
        ae = origin.info[AutoExitInfo.TYPE]

        if not origin.order:
            return False

        if origin.order.status in STATUS_FINISHED and self._CLOSE_TAG in origin.info:
            if self.orderClosed(origin):
                del self._autoExitInfo[ae.originID]
                logging.info("%s | %s | %s closed | remove AutoExitInfo", self.currentTime, ae, showOrder(origin.order, "vtOrderID"))
                return False
    
        if origin.order.direction == constant.DIRECTION_LONG:
            if ae.stoploss and (ae.stoploss >= bid):
                self.composoryClose(origin)
                del self._autoExitInfo[ae.originID]
                logging.info(
                    "%s | %s | StopLoss of %s triggered on %s", 
                    self.currentTime, ae, showOrder(origin.order, "vtOrderID", "price_avg"), bid
                )
                return True
    
        elif origin.order.direction == constant.DIRECTION_SHORT:
            if ae.stoploss and (ae.stoploss <= ask):
                self.composoryClose(origin)
                del self._autoExitInfo[ae.originID]
                logging.info(
                    "%s | %s | StopLoss of %s triggered on %s", 
                    self.currentTime, ae, showOrder(origin.order, "vtOrderID", "price_avg"), ask
                )
                return True       
        
        if ae.takeprofit and ae.check_tp:
            for op in self.iterValidOrderPacks(*ae.tpOrderIDs):
                if op.order.price != ae.takeprofit:
                    if op.order.status in STATUS_FINISHED:
                        ae.tpOrderIDs.discard(op.vtOrderID)
                        continue
                    logging.info(
                        "%s | %s | cancel invalid takeprofit pending order(vtOrderID=%s, price=%s) for %s", 
                        self.currentTime, ae, op.vtOrderID, op.order.price, origin.vtOrderID
                    )
                    self.cancelOrder(op.vtOrderID)
            unlocked = self.orderUnlockedVolume(origin)
            if unlocked and self.isPendingPriceValid(self.getCloseOrderType(origin.order), origin.order.vtSymbol, ae.takeprofit):
                logging.info(
                    "%s  | %s | send takeprofit(volume=%s) for %s", 
                    self.currentTime, ae, unlocked, origin.vtOrderID
                )
                ae.takeprofit = self.adjustPrice(origin.order.vtSymbol, ae.takeprofit, "takeprofit")
                for pack in self.closeOrder(origin, ae.takeprofit, unlocked):
                    ae.tpOrderIDs.add(pack.vtOrderID)
                    pack.addTrack(AutoExitInfo.TP_TAG, ae)
        else:
            for op in self.iterValidOrderPacks(*ae.tpOrderIDs):
                self.cancelOrder(op.vtOrderID)
        return False

    def onTakeProfitPending(self, op):
        ae = op.info[AutoExitInfo.TP_TAG]
        if op.order.status in STATUS_FINISHED:
            logging.info("%s | %s | takeprofit pending order finished | %s", self.currentTime, ae, showOrder(op.order, "vtOrderID", "status", "price_avg"))
            ae.tpOrderIDs.discard(op.vtOrderID)
            if op.order.status == constant.STATUS_CANCELLED and not self.isCancel(op):
                ae.check_tp = False
                logging.warning("%s | %s | TakeProfit order unexpectedly canceled | %s", self.currentTime, ae, showOrder(op.order, "vtOrderID", "vtSymbol", "price", "volume"))

    def checkAutoExit(self, vtSymbol, check_tp=False):
        if vtSymbol in self._tickInstance:
            tick = self._tickInstance[vtSymbol]
            ask, bid = tick.askPrice1, tick.bidPrice1
        elif vtSymbol in self._barInstance:
            bar = self._barInstance[vtSymbol]
            ask = bar.high
            bid = bar.low
        else:
            return
        for op in list(self._autoExitInfo.values()):
            
            if op.order.vtSymbol == vtSymbol:
                self.execAutoExit(op, ask, bid, check_tp)
    
    def checkTakeProfit(self, vtSymbol):
        self.checkAutoExit(vtSymbol, True)
    
    def checkStepOrders(self, vtSymbol):
        pool = self._infoPool[StepOrderInfo.TYPE].get(vtSymbol, None)
        
        if not pool:
            return
        for soi in list(pool.values()):
            
            if soi.expire_at < self.currentTime:
                pool.pop(id(soi))
                continue
            self.execStepOrder(soi)

    def execStepOrder(self, soi):
        assert isinstance(soi, StepOrderInfo)
        if self.currentTime < soi.nextSendTime:
            return
        
        locked = self.aggOrder(soi.vtOrderIDs, "totalVolume", sum) + self.aggOrder(soi.finishedOrderIDs, 'tradedVolume', sum)
        locked = self._round(locked)
        if locked < soi.volume:
            
            volume = soi.step if locked + soi.step <= soi.volume else soi.volume - locked
            tlo = self.timeLimitOrder(soi.orderType, soi.vtSymbol, soi.price, volume, (soi.expire_at - self.currentTime).total_seconds())
            for pack in self.findOrderPacks(tlo.vtOrderIDs):
                pack.addTrack(StepOrderInfo.TYPE, soi)
                soi.add(pack)
            soi.nextSendTime = self.currentTime + timedelta(seconds=soi.wait)
                    
    def onStepOrder(self, op):
        soi = op.info[StepOrderInfo.TYPE]
        if op.order.status in STATUS_FINISHED:
            soi.finish(op)
            traded = self._round(self.aggOrder(soi.finishedOrderIDs, "tradedVolume", sum))

            if not soi.vtOrderIDs and (traded == soi.volume):
                self._infoPool[StepOrderInfo.TYPE].get(op.order.vtSymbol, {}).pop(id(soi), None)
            
    def makeStepOrder(self, orderType, vtSymbol, price, volume, step, expire, wait=0):
        expire_at = self.currentTime + timedelta(seconds=expire)
        volume = self._round(volume)
        soi = StepOrderInfo(orderType, vtSymbol, price, volume, step, expire_at, wait)
        if self.getEngineType() == ctaBase.ENGINETYPE_TRADING:
            self._infoPool[StepOrderInfo.TYPE].setdefault(vtSymbol, {})[id(soi)] = soi
        else:
            vtOrderIDs = self.timeLimitOrder(orderType, vtSymbol, price, volume, expire).vtOrderIDs
            for pack in self.findOrderPacks(vtOrderIDs):
                pack.addTrack(soi.TYPE, soi)
                soi.add(pack)
        return soi
    
    def makeDepthOrder(self, orderType, vtSymbol, price, volume, depth, expire, wait=0):
        expire_at = self.currentTime + timedelta(seconds=expire)
        doi = DepthOrderInfo(orderType, vtSymbol, price, volume, depth, expire_at, wait)
        if self.getEngineType() == ctaBase.ENGINETYPE_TRADING:
            self._infoPool[DepthOrderInfo.TYPE].setdefault(vtSymbol, {})[id(doi)] = doi
        else:
            vtOrderIDs = self.timeLimitOrder(orderType, vtSymbol, price, volume, expire).vtOrderIDs
            for pack in self.findOrderPacks(vtOrderIDs):
                pack.addTrack(doi.TYPE, doi)
                doi.add(pack)
        return doi
    
    def checkDepthOrders(self, vtSymbol):
        pool = self._infoPool[DepthOrderInfo.TYPE].get(vtSymbol, None)
        if not pool:
            return
        tick = self._tickInstance[vtSymbol]
        for doi in list(pool.values()):
            
            if doi.expire_at < self.currentTime:
                pool.pop(id(doi))
                continue
            self.execDepthOrder(doi, tick)

    def execDepthOrder(self, doi, tick):
        assert isinstance(tick, VtTickData)
        assert isinstance(doi, DepthOrderInfo)
        
        if self.currentTime < doi.nextSendTime:
            return
        
        locked = self.aggOrder(doi.vtOrderIDs, "totalVolume", sum) + self.aggOrder(doi.finishedOrderIDs, 'tradedVolume', sum)
        unlocked = self._round(doi.volume - locked)
        if unlocked <= 0:
            return
        executable = 0
        for p, v in doi.keys:
            price = getattr(tick, p, None)
            volume = getattr(tick, v, None)
            if doi.isPriceExecutable(price):
                executable += volume
                if executable >  unlocked:
                    executable = unlocked
                    break
            else:
                break
        if executable <= 0:
            return
        
        tlo = self.timeLimitOrder(doi.orderType, doi.vtSymbol, doi.price, executable, (doi.expire_at - self.currentTime).total_seconds())
        for pack in self.findOrderPacks(tlo.vtOrderIDs):
            pack.addTrack(DepthOrderInfo.TYPE, doi)
            doi.add(pack)
        doi.nextSendTime = self.currentTime + timedelta(seconds=doi.wait)

    def onDepthOrder(self, op):
        doi = op.info[DepthOrderInfo.TYPE]
        if op.order.status in STATUS_FINISHED:
            doi.finish(op)

    def aggOrder(self, vtOrderIDs, name, func):
        l = []
        for vtOrderID in vtOrderIDs:
            pack = self._orderPacks.get(vtOrderID, None)
            if not (pack and pack.order):
                continue
            l.append(getattr(pack.order, name))
        return func(l)
    
    def iterValidOrderPacks(self, *vtOrderIDs):
        for vtOrderID in vtOrderIDs:
            if vtOrderID in self._orderPacks:
                yield self._orderPacks[vtOrderID]
    
    def findOrderPacks(self, vtOrderIDs):
        if isinstance(vtOrderIDs, str):
            return tuple(self.iterValidOrderPacks(vtOrderIDs))
        elif isinstance(vtOrderIDs, Iterable):
            return tuple(self.iterValidOrderPacks(*vtOrderIDs))
        else:
            return tuple()

    def onBar(self, bar):
        self.updateBar(bar)

    def updateBar(self, bar):
        self._currentTime = bar.datetime
        self._barInstance[bar.vtSymbol] = bar

    def onTick(self, tick):
        self._currentTime = tick.datetime
        self._tickInstance[tick.vtSymbol] = tick
    
    @property
    def currentTime(self):
        if self.getEngineType() == "trading":
            return datetime.now()
        else:
            return self._currentTime

    def getExecPrice(self, vtSymbol, orderType):
        if orderType in self._ORDERTYPE_LONG:
            if vtSymbol in self._tickInstance:
                return self._tickInstance[vtSymbol].upperLimit*0.99
            elif vtSymbol in self._barInstance:
                return self._barInstance[vtSymbol].high
            else:
                return None

        elif orderType in self._ORDERTYPE_SHORT:
            if vtSymbol in self._tickInstance:
                return self._tickInstance[vtSymbol].lowerLimit*1.01
            elif vtSymbol in self._barInstance:
                return self._barInstance[vtSymbol].low
            else:
                return None
        
        else:
            return None

    def getCurrentPrice(self, vtSymbol):
        if vtSymbol in self._tickInstance:
            return self._tickInstance[vtSymbol].lastPrice
        elif vtSymbol in self._barInstance:
            return self._barInstance[vtSymbol].close
        else:
            return None
    
    # 检查OrderPack对应的订单是否完全平仓
    def orderClosed(self, op):
        if ComposoryOrderInfo.CPO_CLOSED in op.info:
            return True

        if op.order.status not in STATUS_FINISHED:
            return False
        
        if op.order.tradedVolume == 0:
            return True

        if self._CLOSE_TAG not in op.info:
            return False

        return op.order.tradedVolume == self.orderClosedVolume(op)

    def setConditionalClose(self, op, expire, targetProfit=None):
        coc = ConditionalOrderClose(op.vtOrderID, self.currentTime+timedelta(seconds=float(expire)), targetProfit)
        op.info[ConditionalOrderClose.TYPE] = coc
        self._infoPool[ConditionalOrderClose.TYPE][coc.originID] = coc
        logging.info("%s | %s | set conditional close on %s", self.currentTime,  coc, showOrder(op.order, "vtOrderID"))
    
    def checkConditionalClose(self):
        pool = self._infoPool[ConditionalOrderClose.TYPE]
        for coc in list(pool.values()):
            if self.currentTime >= coc.expire_at:
                op = self._orderPacks[coc.originID]

                if op.order.status not in STATUS_FINISHED:
                    logging.info("%s | %s |  Open%s not finished | cancel OpenOrder", 
                        self.currentTime, coc, showOrder(op.order, "vtOrderID", "status")
                    )
                    self.cancelOrder(op.vtOrderID)
                    continue
                
                if not op.order.tradedVolume:
                    logging.info("%s | %s | Open%s not traded | process finished", 
                        self.currentTime, coc, showOrder(op.order, "vtOrderID", "status", "tradedVolume")
                    )
                    pool.pop(op.vtOrderID, None)
                    continue

                if coc.targetProfit is None:
                    logging.info("%s | %s | exceeded time limit | close %s", 
                        self.currentTime, coc, showOrder(op.order, "vtOrderID")
                    )
                    self.composoryClose(op)
                else:
                    if op.order.direction == constant.DIRECTION_LONG:
                        direction = 1
                    elif op.order.direction == constant.DIRECTION_SHORT:
                        direction = -1
                    else:
                        raise ValueError("Invalid direction: %s, %s" % op.order.direction, op.order.__dict__)
                    
                    stoplossPrice = op.order.price_avg * (1 + direction*coc.targetProfit)
                    self.setAutoExit(op, stoplossPrice)
                    logging.info("%s | %s | exceeded time limit | set stoploss for %s at : %s", 
                        self.currentTime, coc,showOrder(op.order, "vtOrderID", "price_avg"), stoplossPrice
                    )
                    curentPrice = self.getCurrentPrice(op.order.vtSymbol)
                    self.execAutoExit(op, curentPrice, curentPrice)

                pool.pop(op.vtOrderID, None)

    def checkOnPeriodStart(self, bar):
        self.checkComposoryOrders()
        self.checkTimeLimitOrders()
        self.checkAutoExit(bar.vtSymbol)
        self.checkConditionalClose()

    def checkOnPeriodEnd(self, bar):
        self.checkComposoryCloseOrders(bar.vtSymbol)
        self.checkDepthOrders(bar.vtSymbol)
        self.checkStepOrders(bar.vtSymbol)

    def splitOrder(self, op, *volumes):
        if op.order.status not in STATUS_FINISHED:
            return []
        
        order = op.order
        soi = AssembleOrderInfo()
        soi.originID = op.vtOrderID
        op.info[AssembleOrderInfo.TYPE] = soi
        totalVolume = order.tradedVolume
        count = 0
        results = []
        for volume in volumes:
            if totalVolume <= 0:
                break
            if totalVolume < volume:
                volume = totalVolume
            fakeOrder = VtOrderData()
            fakeOrder.vtOrderID = order.vtOrderID + "-%d" % count
            fakeOrder.status = constant.STATUS_ALLTRADED
            fakeOrder.totalVolume = volume
            fakeOrder.tradedVolume = volume
            fakeOrder.direction = order.direction
            fakeOrder.offset = order.offset
            fakeOp = OrderPack(fakeOrder.vtOrderID)
            fakeOp.order = fakeOrder
            self._orderPacks[fakeOp.vtOrderID] = fakeOp
            results.append(fakeOp)
            totalVolume -= volume
            count += 1
        if totalVolume > 0:
            fakeOrder = VtOrderData()
            fakeOrder.vtOrderID = order.vtOrderID + "-%d" % count
            fakeOrder.status = constant.STATUS_ALLTRADED
            fakeOrder.totalVolume = totalVolume
            fakeOrder.tradedVolume = totalVolume
            fakeOp = OrderPack(fakeOrder.vtOrderID)
            fakeOp.order = fakeOrder
            self._orderPacks[fakeOp.vtOrderID] = fakeOp
            results.append(fakeOp)

        for fop in results:
            soi.childIDs.add(fop.vtOrderID)
            fop.info[AssembleOrderInfo.TYPE] = soi
            fop.info[AssembleOrderInfo.TAG] = AssembleOrderInfo.CHILD
        return results

    def isComposory(self, op):
        return ComposoryOrderInfo.TYPE in op.info
    
    def isTimeLimit(self, op):
        return TimeLimitOrderInfo.TYPE in op.info
    
    def isAutoExit(self, op):
        return AutoExitInfo.TYPE in op.info
    
    def isClosingPending(self, op):
        return bool(op.info.get(self._CLOSE_TAG, None))
    
    def isAssembled(self, op):
        return AssembleOrderInfo.TYPE in op.info
    
    def isAssembleOrigin(self, op):
        return op.info.get(AssembleOrderInfo.TAG, None) == AssembleOrderInfo.ORIGIN

    def isAssembleChild(self, op):
        return op.info.get(AssembleOrderInfo.TAG, None) == AssembleOrderInfo.CHILD

    def isCloseOrder(self, op):
        return op.order.offset == constant.OFFSET_CLOSE and (self._OPEN_TAG in op.info)
    
    def hasCloseOrder(self, op):
        return op.order.offset == constant.OFFSET_OPEN and (self._CLOSE_TAG in op.info)
    
    def findOpenOrderPack(self, closeOrderPack):
        if self.isCloseOrder(closeOrderPack):
            return self._orderPacks[closeOrderPack.info[self._OPEN_TAG]]
    
    def listCloseOrderPack(self, openOrderPack):
        if self.isClosingPending(openOrderPack):
            return list(self.iterValidOrderPacks(*openOrderPack.info[self._CLOSE_TAG]))
        else:
            return []
    
    def isPendingPriceValid(self, orderType, vtSymbol, price):
        current = self.getCurrentPrice(vtSymbol)
        direction = DIRECTION_MAP[orderType]
        if direction == constant.DIRECTION_LONG:
            return current*self.UPPER_LIMIT >= price
        elif direction == constant.DIRECTION_SHORT:
            return current*self.LOWER_LIMIT <= price
        else:
            return False

    def adjustPrice(self, vtSymbol, price, tag=""):
        mode = self.getEngineType()
        if mode == ctaBase.ENGINETYPE_TRADING:
            contract = self.ctaEngine.mainEngine.getContract(vtSymbol)
            result = self.ctaEngine.roundToPriceTick(contract.priceTick, price)
        elif mode == ctaBase.ENGINETYPE_BACKTESTING:
            result = self.ctaEngine.roundToPriceTick(price)
        else:
            result = price
        
        if result != price:
            logging.warning("Adjust price | %s | %s => %s | %s", vtSymbol, price, result, tag)

        return result

    @staticmethod
    def getCloseOrderType(order):
        if order.offset == constant.OFFSET_OPEN:
            if order.direction == constant.DIRECTION_LONG:
                return ctaBase.CTAORDER_SELL
            elif order.direction == constant.DIRECTION_SHORT:
                return ctaBase.CTAORDER_COVER
            else:
                raise ValueError("Invalid direction: %s" % order.direction)
        else:
            raise ValueError("Invalid offset: %s" % order.offset)
    
    def cancelOrder(self, vtOrderID):
        if vtOrderID in self._orderPacks:
            self._orderPacks[vtOrderID].info[self._CANCEL_TAG] = True
        return super().cancelOrder(vtOrderID)
    
    def isCancel(self, op):
        return op.info.get(self._CANCEL_TAG, False)

    def maximumOrderVolume(self, vtSymbol, orderType, price=None):
        return np.inf

    def isOrderVolumeValid(self, vtSymbol, orderType, volume, price=None):
        if volume <=0:
            return False
        
        maximum = self.maximumOrderVolume(vtSymbol, orderType, price)
        return maximum >= volume
