import requests
import json
import argparse
import chess
import chess.engine
import chess.svg
import math
import sys
import concurrent.futures
import multiprocessing
import copy
import hashlib
from urllib import parse


class ChessGraph:
    def __init__(
        self, depth, concurrency, source, engine, enginedepth, boardstyle, boardedges
    ):
        self.visited = set()
        self.depth = depth
        self.executorgraph = [
            concurrent.futures.ThreadPoolExecutor(max_workers=concurrency)
            for i in range(0, depth + 1)
        ]
        self.executorwork = concurrent.futures.ThreadPoolExecutor(
            max_workers=concurrency
        )
        self.session = requests.Session()
        self.source = source
        self.engine = engine
        self.enginedepth = enginedepth
        self.boardstyle = boardstyle
        self.boardedges = boardedges

    def get_moves(self, epd):

        if self.source == "chessdb":
            return self.get_moves_chessdb(epd)
        elif self.source == "engine":
            return self.get_moves_engine(epd)
        else:
            assert False

    def get_moves_engine(self, epd):

        moves = []
        engine = chess.engine.SimpleEngine.popen_uci(self.engine)
        board = chess.Board(epd)
        info = engine.analyse(
            board,
            chess.engine.Limit(depth=self.enginedepth),
            multipv=10,
            info=chess.engine.INFO_SCORE | chess.engine.INFO_PV,
        )
        engine.quit()
        for i in info:
            moves.append(
                {
                    "score": i["score"].pov(board.turn).score(mate_score=30000),
                    "uci": chess.Move.uci(i["pv"][0]),
                }
            )

        return moves

    def get_moves_chessdb(self, epd):

        api = "http://www.chessdb.cn/cdb.php"
        url = api + "?action=queryall&board=" + parse.quote(epd) + "&json=1"
        timeout = 3

        moves = []

        try:
            response = self.session.get(url, timeout=timeout)
            response.raise_for_status()
            data = response.json()
            if data["status"] == "ok":
                moves = data["moves"]
            elif data["status"] == "unknown":
                pass
            elif data["status"] == "rate limited exceeded":
                sys.stderr.write("rate")
            else:
                sys.stderr.write(data)
        except:
            pass

        stdmoves = []
        for m in moves:
            stdmoves.append({"score": m["score"], "uci": m["uci"]})

        return stdmoves

    def write_node(self, board, score, showboard, pvNode):

        if board.turn == chess.WHITE:
            color = ", color=gold, shape=box"
        else:
            color = ", color=burlywood4, shape=box"

        if pvNode:
            width = ", penwidth=3"
        else:
            width = ", penwidth=1"

        epd = board.epd()
        epdweb = parse.quote(epd)
        url = ', URL="https://www.chessdb.cn/queryc_en/?' + epdweb + '"'
        if showboard and not self.boardstyle == "none":
            if self.boardstyle == "unicode":
                label = (
                    'fontname="Courier", label="'
                    + board.unicode(empty_square="\u00B7")
                    + '"'
                )
            elif self.boardstyle == "svg":
                filename = (
                    "node-" + hashlib.sha256(epd.encode("utf-8")).hexdigest() + ".svg"
                )
                # this prefix seems to be needed to enable dot to include the svg:
                # https://stackoverflow.com/questions/49819164/graphviz-nodes-of-svg-images-do-not-get-inserted-if-output-is-svg
                # strangely, only with the newline this works, and yet, dot appears to ignore the size attribute
                prefix = '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
                with open(filename, "w", encoding="utf-8") as f:
                    f.write(prefix + chess.svg.board(board, size="100px"))
                label = 'label="", image="' + filename + '"'
        else:
            label = 'label="' + str(score) + '"'

        return '"' + epd + '" [' + label + color + url + width + "]"

    def write_edge(self, epdfrom, epdto, move, turn, pvEdge):

        if turn == chess.WHITE:
            color = ", color=gold"
        else:
            color = ", color=burlywood4"

        if pvEdge:
            width = ', penwidth=3, fontname="Helvetica-bold"'
        else:
            width = ', penwidth=1, fontname="Helvectica"'

        return (
            '"'
            + epdfrom
            + '" -> "'
            + epdto
            + '" [label="'
            + move
            + '"'
            + color
            + width
            + "]"
        )

    def recurse(self, board, depth, alpha, beta, pvNode):

        epdfrom = board.epd()
        returnstr = []

        # terminate recursion if visited
        if epdfrom in self.visited:
            return returnstr
        else:
            self.visited.add(epdfrom)

        turn = board.turn
        moves = self.executorwork.submit(self.get_moves, epdfrom).result()
        bestscore = None
        edgesfound = 0
        edgesdrawn = 0
        futures = []
        edges = []

        # loop through the moves that are within delta of the bestmove
        for m in sorted(moves, key=lambda item: item["score"], reverse=True):

            score = int(m["score"])

            if bestscore == None:
                bestscore = score

            if score <= alpha:
                break

            ucimove = m["uci"]
            move = chess.Move.from_uci(ucimove)
            sanmove = board.san(move)
            board.push(move)
            epdto = board.epd()
            edgesfound += 1
            pvEdge = pvNode and score == bestscore

            # no loops, otherwise recurse
            if score == bestscore:
                newDepth = depth - 1
            else:
                newDepth = depth - int(1.5 + math.log(edgesfound) / math.log(2))

            if newDepth > 0:
                if not epdto in self.visited:
                    futures.append(
                        self.executorgraph[depth].submit(
                            self.recurse,
                            copy.deepcopy(board),
                            newDepth,
                            -beta,
                            -alpha,
                            pvEdge,
                        )
                    )
                edgesdrawn += 1
                returnstr.append(self.write_edge(epdfrom, epdto, sanmove, turn, pvEdge))

            board.pop()

        for f in futures:
            returnstr += f.result()

        returnstr.append(
            self.write_node(
                board,
                bestscore,
                edgesdrawn >= self.boardedges or (pvNode and edgesdrawn == 0),
                pvNode,
            )
        )

        return returnstr

    def generate_graph(self, epd, alpha, beta):

        # set initial board
        board = chess.Board(epd)

        dotstr = ["digraph {"]
        dotstr += self.recurse(board, self.depth, alpha, beta, pvNode=True)
        dotstr.append(self.write_node(board, 0, True, True))
        dotstr.append("}")

        return dotstr


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--depth",
        type=int,
        default=6,
        help="Maximum depth (in plies) of a followed variation",
    )

    parser.add_argument(
        "--alpha",
        type=int,
        default=0,
        help="Lower bound on the score of variations to be followed",
    )

    parser.add_argument(
        "--beta",
        type=int,
        default=15,
        help="Upper bound on the score of variations to be followed",
    )

    parser.add_argument(
        "--concurrency",
        type=int,
        default=multiprocessing.cpu_count(),
        help="Number of cores to use for work / requests.",
    )

    parser.add_argument(
        "--position",
        type=str,
        default="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        help="FEN of the starting position.",
    )

    parser.add_argument(
        "--source",
        choices=["chessdb", "engine"],
        type=str,
        default="chessdb",
        help="Use chessdb or engine to score and rank moves",
    )

    parser.add_argument(
        "--boardstyle",
        choices=["unicode", "svg", "none"],
        type=str,
        default="unicode",
        help="Which style to use to visualize a board.",
    )

    parser.add_argument(
        "--boardedges",
        type=int,
        default=3,
        help="Minimum number of edges needed before a board is visualized in the node.",
    )

    parser.add_argument(
        "--engine",
        type=str,
        default="stockfish",
        help="Name of the engine binary (with path as needed).",
    )

    parser.add_argument(
        "--enginedepth",
        type=int,
        default=20,
        help="Depth of the search used by the engine in evaluation",
    )

    args = parser.parse_args()

    chessgraph = ChessGraph(
        args.depth,
        args.concurrency,
        args.source,
        args.engine,
        args.enginedepth,
        args.boardstyle,
        args.boardedges,
    )

    # generate the content of the dotfile
    dotstr = chessgraph.generate_graph(args.position, args.alpha, args.beta)

    # write it
    for line in dotstr:
        print(line)