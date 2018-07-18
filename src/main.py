
import argparse



def main():
    argparser = argparse.ArgumentParser()
    argparser.add_argument('torrent_path', help='path to the .torrent file')
    args = argparser.parse_args()
    print(args.torrent_path)


if __name__ == '__main__':
    main()
